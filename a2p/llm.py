"""The text model, run through ComfyUI (workflows/text.json: a model
loader, a text-generate node with {{prompt}} / {{system_prompt}}
placeholders and a PreviewAny node the reply is read from).

It only ever returns labels (a genre name, sentence numbers); it never
rewrites article text. Every caller has a fallback for when it fails.
"""
import json, time
from . import comfy, sentences as S, settings

loaded = False      # True once the text model has run, until someone frees ComfyUI's memory


def ask(prompt, system_prompt):
    """(reply text, error). Never raises."""
    global loaded
    try:
        wf = comfy.load_workflow('text')
    except Exception as e:
        return None, f'could not load workflows/text.json: {e}'
    fills = {'{{prompt}}': prompt, '{{system_prompt}}': system_prompt}
    for node in wf.values():
        for key, val in node.get('inputs', {}).items():
            if isinstance(val, str) and val in fills:
                node['inputs'][key] = fills[val]
    reply_id, _ = comfy.find_node(wf, 'PreviewAny')
    if not reply_id:
        return None, 'workflows/text.json has no PreviewAny node to read the reply from'
    res = comfy.run(wf, timeout=int(settings.load()['text_model_timeout']))
    loaded = loaded or res.status != 'cancelled'
    if not res.ok:
        return None, res.detail or res.status
    text = comfy.output_text(res.entry, reply_id)
    return (text, '') if text else (None, 'the text model returned nothing')


def parse_json(text):
    """The first JSON object in a model reply (after any </think> block)."""
    if not text:
        return None
    text = text.split('</think>')[-1]
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def system_prompt(genres):
    """Instructions for the genre + quotes question. The genre list and
    descriptions come from settings; with no genres only quotes are asked."""
    if genres:
        genre_part = ('1. GENRE: pick exactly one of: ' + ', '.join(genres) + '.\n' +
                      ''.join(f'   - {g}: {v["description"]}\n' if v.get('description') else f'   - {g}\n'
                              for g, v in genres.items()) + '\n')
        ask_line, qnum = 'Answer two questions.', '2'
        reply = '{"genre": "...", "tone": ["..."], "quotes": [sentence numbers]}'
    else:
        genre_part, ask_line, qnum = '', 'Answer this question.', '1'
        reply = '{"tone": ["..."], "quotes": [sentence numbers]}'
    return (
        'You prepare articles to be read aloud by a single narrator. You are shown the article\'s '
        'metadata and its sentences, numbered. ' + ask_line + '\n\n' + genre_part +
        qnum + '. QUOTES: list the numbers of sentences that are mainly a direct quotation of words someone '
        'said or wrote (a statement, testimony, a post, a letter, a passage from another publication). '
        'Include every sentence of a quotation that runs over several sentences. Do NOT include sentences '
        'that are the writer\'s own words containing a short quoted phrase, a nickname, a title or scare '
        'quotes. Sentences marked [BLOCK QUOTE] are already quotes; you do not need to list them.\n\n'
        'Also give 1 or 2 words for the writer\'s tone (for example: neutral, sardonic, outraged, playful, '
        'wistful, enthusiastic).\n\n'
        'Reply with ONLY a JSON object, no commentary:\n' + reply
    )


def _prompt(job, sents, first, last):
    head = f'title: {job["title"]}\nauthor: {job["author"]}\nsite: {job["site"]}\n'
    if job.get('url'):
        head += f'source: {job["url"]}\n'
    lines = []
    for i in range(first, last):
        s = sents[i]
        words = s['text'].split()
        text  = s['text'] if len(words) <= 45 else ' '.join(words[:38]) + ' ... ' + ' '.join(words[-6:])
        lines.append(f'[{i + 1}]{" [BLOCK QUOTE]" if s["block_quote"] else ""} {text}')
    return 'METADATA\n' + head + '\nSENTENCES\n' + '\n'.join(lines) + '\n\nReturn the JSON object now.'


def analyze(job, sents, pinned_genre=None, window=90):
    """Decide the article's genre and mark which sentences are quotes
    (sets s['quote']). Returns {genre, genre_by, quotes_by, tone, seconds,
    errors}. Falls back to default_genre and the quotation-mark rule."""
    s = settings.load()
    genres = {str(k).strip().lower(): v for k, v in s['genres'].items()}
    info = {'genre': pinned_genre if pinned_genre in genres else None,
            'genre_by': 'site' if pinned_genre in genres else 'default',
            'quotes_by': 'marks', 'tone': [], 'seconds': 0.0, 'errors': []}
    S.mark_quotes_heuristic(sents)
    if s['text_model'] and sents and comfy.has_workflow('text') and not job.get('skip_text_model'):
        want_genre = bool(genres) and info['genre'] is None
        picked, ok, started = set(), 0, time.time()
        starts = list(range(0, len(sents), window))
        for first in starts:
            last = min(len(sents), first + window)
            reply, err = ask(_prompt(job, sents, first, last), system_prompt(genres if want_genre else {}))
            data = parse_json(reply) if reply else None
            if not data:
                info['errors'].append(err or 'the reply was not valid JSON')
                continue
            ok += 1
            g = str(data.get('genre') or '').strip().lower().replace('/', '_').replace(' ', '_')
            if want_genre and info['genre'] is None and g in genres:
                info['genre'], info['genre_by'] = g, 'model'
                tone = data.get('tone') or []
                info['tone'] = [str(t)[:20] for t in (tone if isinstance(tone, list) else [tone])][:2]
            for n in data.get('quotes') or []:
                if str(n).isdigit() and first < int(n) <= last:
                    picked.add(int(n) - 1)
        info['seconds'] = round(time.time() - started, 1)
        if ok == len(starts):
            # Trust the model's picks only where quotation marks back them up.
            for i, sent in enumerate(sents):
                if sent['block_quote']:
                    sent['quote'] = True
                elif i in picked:
                    sent['quote'] = sent['qwords'] >= 4 and sent['qwords'] / max(1, sent['words']) >= 0.5
                else:
                    sent['quote'] = False
            info['quotes_by'] = 'model'
    if info['genre'] is None:
        info['genre'] = s['default_genre'] if s['default_genre'] in genres else ''
    return info
