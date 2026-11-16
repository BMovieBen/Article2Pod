# breeze_text.py
#
# Text analysis for the Breeze TTS path:
#   - parse the article file into a title line and paragraphs, keeping
#     block quotes (indented lines, as fetch-article.py writes them)
#   - split paragraphs into sentences and mark which ones are quotes
#   - ask the text model (same ComfyUI workflow as ai_cleanup) for the
#     article's genre and which sentences are quoted speech
#
# The model never rewrites text: it only returns a genre name and sentence
# numbers. Its quote picks are checked against the actual quotation marks,
# and if it fails the quotation-mark heuristic is used instead.

import re, json, time
from utils import get_breeze_genres

PAUSE_RE   = re.compile(r'\s*\[pause:(\d+)\]\s*$')
SENT_SPLIT = re.compile(r'(?<=[.!?”"\'’])\s+(?=[A-Z0-9“"‘(])')
ABBREV     = re.compile(r'\b(?:Mr|Mrs|Ms|Dr|St|Sen|Rep|Gov|Gen|Lt|Col|Sgt|Capt|Prof|Jr|Sr|U\.S|U\.K|No|vs|etc|Inc|Co|Corp|Jan|Feb|Mar|Apr|Aug|Sept|Sep|Oct|Nov|Dec)\.$')


# ------------------------------------------------------------
# Article file -> paragraphs
# ------------------------------------------------------------
def parse_article(raw):
    """Returns dict(title, byline, paragraphs=[(text, is_block_quote)], end_pause_ms)."""
    text = raw.replace('\r\n', '\n').replace('\r', '\n')
    end_pause = 0
    m = PAUSE_RE.search(text)
    if m:
        end_pause, text = int(m.group(1)), text[:m.start()]
    lines  = text.split('\n')
    title  = lines[0].strip() if lines else ''
    rest   = lines[1:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    byline = ''
    if rest and rest[0].strip().lower().startswith('written by'):
        byline, rest = rest[0].strip(), rest[1:]
    paragraphs = []
    for line in rest:
        if not line.strip():
            continue
        is_bq = line.startswith('    ') or line.startswith('\t')
        paragraphs.append((line.strip(), is_bq))
    return {'title': title, 'byline': byline, 'paragraphs': paragraphs, 'end_pause_ms': end_pause}


def split_sentences(paragraph):
    """Sentence split that does not break after common abbreviations."""
    parts, out = SENT_SPLIT.split(paragraph.strip()), []
    for p in parts:
        if out and ABBREV.search(out[-1]):
            out[-1] += ' ' + p
        else:
            out.append(p)
    return [s for s in out if s.strip()]


# ------------------------------------------------------------
# Quotation marks
# ------------------------------------------------------------
def quoted_mask(p):
    """Per-character True where inside a quotation (straight or curly), so
    quotes that run across sentence breaks are covered too."""
    mask, inside = [False] * len(p), False
    for i, ch in enumerate(p):
        if ch == '“':
            inside = True
        elif ch == '”':
            mask[i] = True
            inside = False
            continue
        elif ch == '"':
            if inside:
                mask[i] = True
                inside = False
                continue
            inside = True
        mask[i] = inside
    return mask


def quoted_fraction(paragraph, sentence, start):
    """(quoted words, total words) for one sentence inside its paragraph."""
    mask  = quoted_mask(paragraph)
    words = list(re.finditer(r'\S+', sentence))
    q = sum(1 for w in words
            if mask[min(start + w.start(), len(mask) - 1)]
            or mask[min(start + w.start() + 1, len(mask) - 1)])
    return q, len(words)


def build_sentences(parsed):
    """Flat list of sentence dicts: text, para (index), block_quote, qwords, words."""
    out = []
    for pi, (para, is_bq) in enumerate(parsed['paragraphs']):
        pos = 0
        for s in split_sentences(para):
            start = para.find(s, pos)
            start = pos if start < 0 else start
            pos   = start + len(s)
            qw, w = quoted_fraction(para, s, start)
            out.append({'text': s, 'para': pi, 'block_quote': is_bq,
                        'qwords': qw, 'words': w, 'quote': is_bq})
    return out


def insert_cue(text, cue, min_words=4):
    """Put a spoken cue such as "Quote:" right before the quotation: at the
    first opening quote mark whose quoted stretch has at least min_words
    words (so "Boebert said ... “dark money” ... “long quote”" gets the cue
    before the long quote), else at the start."""
    if not cue:
        return text
    mask = quoted_mask(text)
    i = 0
    while i < len(text):
        if text[i] in '“"' and mask[i] and (i == 0 or not mask[i - 1]):
            j = i + 1
            while j < len(text) and mask[j]:
                j += 1
            if len(text[i:j].split()) >= min_words:
                if i == 0:
                    break
                return text[:i].rstrip() + f' {cue} ' + text[i:]
            i = j
        else:
            i += 1
    return f'{cue} {text}'


def mark_quotes_heuristic(sentences, min_frac=0.6, min_quoted_words=6):
    for s in sentences:
        if s['block_quote']:
            s['quote'] = True
        elif s['words'] and s['qwords'] >= min_quoted_words and s['qwords'] / s['words'] >= min_frac:
            s['quote'] = True
    return sentences


# ------------------------------------------------------------
# LLM: genre + quoted speech
# ------------------------------------------------------------
def build_system_prompt(genres):
    """The text model's instructions. The genre list and descriptions come
    from config.json ("breeze" -> "genres" -> <name> -> "description");
    with no genres only the quotes are asked for."""
    if genres:
        genre_part = ('1. GENRE: pick exactly one of: ' + ', '.join(genres) + '.\n' +
                      ''.join(f'   - {g}: {v["description"]}\n' if v.get('description') else f'   - {g}\n'
                              for g, v in genres.items()) + '\n')
        ask, qnum = 'Answer two questions.', '2'
        reply = '{"genre": "...", "tone": ["..."], "quotes": [sentence numbers]}'
    else:
        genre_part, ask, qnum = '', 'Answer this question.', '1'
        reply = '{"tone": ["..."], "quotes": [sentence numbers]}'
    return (
        'You prepare articles to be read aloud by a single narrator. You are shown the article\'s '
        'metadata and its sentences, numbered. ' + ask + '\n\n' + genre_part +
        qnum + '. QUOTES: list the numbers of sentences that are mainly a direct quotation of words someone '
        'said or wrote (a statement, testimony, a post, a letter, a passage from another publication). '
        'Include every sentence of a quotation that runs over several sentences. Do NOT include sentences '
        'that are the writer\'s own words containing a short quoted phrase, a nickname, a title or scare '
        'quotes. Sentences marked [BLOCK QUOTE] are already quotes; you do not need to list them.\n\n'
        'Also give 1 or 2 words for the writer\'s tone (for example: neutral, sardonic, outraged, playful, '
        'wistful, enthusiastic).\n\n'
        'Reply with ONLY a JSON object, no commentary:\n' + reply
    )


def build_prompt(meta, sentences, first, last):
    head = (f'title: {meta.get("title", "")}\n'
            f'author: {meta.get("author", "")}\n'
            f'site: {meta.get("site", "")}\n')
    if meta.get('source_url'):
        head += f'source: {meta["source_url"]}\n'
    lines = []
    for i in range(first, last):
        s = sentences[i]
        words = s['text'].split()
        text  = s['text'] if len(words) <= 45 else ' '.join(words[:38]) + ' ... ' + ' '.join(words[-6:])
        tag   = ' [BLOCK QUOTE]' if s['block_quote'] else ''
        lines.append(f'[{i + 1}]{tag} {text}')
    return ('METADATA\n' + head + '\nSENTENCES\n' + '\n'.join(lines) +
            '\n\nReturn the JSON object now.')


def analyze(meta, sentences, cfg, genre_pinned=None, window=90):
    """Runs the text model over the sentences (in windows for long
    articles). Fills s['quote'] and returns an info dict with genre, tone,
    method and timings. Never raises."""
    from ai_cleanup import run_llm, parse_llm_json
    info = {'genre': genre_pinned, 'tone': [], 'quote_method': 'marks',
            'genre_method': 'site' if genre_pinned else 'default', 'llm_seconds': 0.0,
            'errors': []}
    genres      = get_breeze_genres(cfg)
    want_genre  = bool(genres) and not genre_pinned and cfg.get('genre_mode', 'llm') == 'llm'
    want_quotes = cfg.get('quote_mode', 'llm') == 'llm'
    if cfg.get('quote_mode') == 'off':
        for s in sentences:
            s['quote'] = s['block_quote']
        info['quote_method'] = 'block quotes only'
    else:
        mark_quotes_heuristic(sentences)
    if not (want_genre or want_quotes) or not sentences:
        return info

    llm_cfg = {'workflow_file': cfg['workflow_file'], 'timeout_seconds': cfg['timeout_seconds']}
    llm_quotes, ok_windows, start_all = set(), 0, time.time()
    starts = list(range(0, len(sentences), window)) if want_quotes else [0]
    for first in starts:
        last  = min(len(sentences), first + window)
        reply, err = run_llm(llm_cfg, build_prompt(meta, sentences, first, last),
                             build_system_prompt(genres if want_genre else {}))
        data  = parse_llm_json(reply) if reply else None
        if not data:
            info['errors'].append(err or 'reply was not valid JSON')
            continue
        ok_windows += 1
        g = str(data.get('genre') or '').strip().lower().replace('/', '_').replace(' ', '_')
        if want_genre and info['genre'] is None and g in genres:
            info['genre'], info['genre_method'] = g, 'llm'
            tone = data.get('tone') or []
            info['tone'] = [str(t)[:20] for t in (tone if isinstance(tone, list) else [tone])][:2]
        for n in data.get('quotes') or []:
            if str(n).isdigit() and first < int(n) <= last:
                llm_quotes.add(int(n) - 1)
    info['llm_seconds'] = round(time.time() - start_all, 1)

    if want_quotes and ok_windows == len(starts):
        # Trust the model's picks only where quotation marks back them up.
        for i, s in enumerate(sentences):
            if s['block_quote']:
                s['quote'] = True
            elif i in llm_quotes:
                # at least half the sentence must be inside quotation marks, so a
                # writer's own line with a quoted phrase stays narration
                s['quote'] = s['qwords'] >= 4 and s['qwords'] / max(1, s['words']) >= 0.5
            else:
                s['quote'] = False
        info['quote_method'] = 'llm'
    return info
