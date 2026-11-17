"""Sentences, quotation marks and segments.

An article body is plain text: one paragraph per line, block quotes
indented by four spaces. This turns it into sentences (each knowing how
much of it sits inside quotation marks) and then into the segments an
engine reads aloud.
"""
import re

TINY_RUN_WORDS = 7     # a narration run shorter than this ("Garcia said.") joins the quote before it

SENT_SPLIT = re.compile(r'(?<=[.!?”"\'’])\s+(?=[A-Z0-9“"‘(])')
ABBREV     = re.compile(r'\b(?:Mr|Mrs|Ms|Dr|St|Sen|Rep|Gov|Gen|Lt|Col|Sgt|Capt|Prof|Jr|Sr|U\.S|U\.K|No|vs|etc|Inc|Co|Corp|Jan|Feb|Mar|Apr|Aug|Sept|Sep|Oct|Nov|Dec)\.$')



def paragraphs(body):
    """[(text, is_block_quote)] for the non-empty lines of an article body."""
    out = []
    for line in body.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if line.strip():
            out.append((line.strip(), line.startswith('    ') or line.startswith('\t')))
    return out


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


def build_sentences(paras):
    """Flat list of sentence dicts: text, para (index), block_quote, qwords, words."""
    out = []
    for pi, (para, is_bq) in enumerate(paras):
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


def segments(title, sents, max_words, cue=''):
    """Group sentences into the pieces an engine reads in one go.

    Consecutive sentences of the same kind (narration or quote) form a run;
    a tiny narration run right after a quote in the same paragraph ("Garcia
    said.") stays with the quote; runs are split so no piece exceeds
    max_words. Returns dicts: kind (title | narr | quote), text,
    quote_start, quote_end."""
    segs = []
    if title:
        segs.append({'kind': 'title', 'text': title, 'quote_start': False, 'quote_end': False})
    runs = []
    for s in sents:
        kind = 'quote' if s['quote'] else 'narr'
        if runs and runs[-1]['kind'] == kind:
            runs[-1]['sents'].append(s)
        else:
            runs.append({'kind': kind, 'sents': [s]})
    folded = []
    for r in runs:
        words = sum(s['words'] for s in r['sents'])
        if folded and r['kind'] == 'narr' and words < TINY_RUN_WORDS \
                and r['sents'][0]['para'] == folded[-1]['sents'][-1]['para']:
            folded[-1]['sents'].extend(r['sents'])
        elif folded and folded[-1]['kind'] == r['kind']:
            folded[-1]['sents'].extend(r['sents'])
        else:
            folded.append(r)
    cue = (cue or '').strip()
    for r in folded:
        pieces, cur, cur_words = [], [], 0
        for s in r['sents']:
            if cur and cur_words + s['words'] > max_words:
                pieces.append(cur)
                cur, cur_words = [], 0
            cur.append(s)
            cur_words += s['words']
        if cur:
            pieces.append(cur)
        for pi, piece in enumerate(pieces):
            parts, prev_para = [], None
            for s in piece:
                if prev_para is not None and s['para'] != prev_para:
                    parts.append('\n')
                elif parts:
                    parts.append(' ')
                parts.append(s['text'])
                prev_para = s['para']
            text = ''.join(parts)
            quote_start = r['kind'] == 'quote' and pi == 0
            if quote_start and cue:
                text = insert_cue(text, cue)
            segs.append({'kind': r['kind'], 'text': text, 'quote_start': quote_start,
                         'quote_end': r['kind'] == 'quote' and pi == len(pieces) - 1})
    return segs


def title_line(title, author):
    """What is spoken first: the title, then "Written by <author>."."""
    title = (title or '').strip()
    if not title:
        return ''
    if title[-1] not in '.!?':
        title += '.'
    if author and author != 'Unknown Author':
        title += f' Written by {author.rstrip(".")}.'
    return title
