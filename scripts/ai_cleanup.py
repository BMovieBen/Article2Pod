# ai_cleanup.py
#
# Optional LLM pass run after the user clicks generate, before audio:
#   1. flags leftover non-article lines (captions, promos, bylines...) to drop
#   2. checks the detected title / author / site and proposes fixes
#
# The model never rewrites the article. It is shown numbered, shortened
# lines and answers with JSON naming line numbers to delete plus corrected
# metadata. Article2Pod performs the deletions itself, so article wording
# cannot be altered, and a result that would remove too much is refused.
#
# Controlled by the "ai_cleanup" block in config.json:
#   mode: off | log_only (decide and log, change nothing) | apply
# User-edited metadata (metadata_edited in the sidecar) is never overwritten.
#
# Usage: python ai_cleanup.py <slug>
# Never fails the pipeline: on any error the original text is left alone.

import os, sys, json, re, time
import requests
from utils import (
    APP_DIR, get_comfy_url, get_temp_folder, get_ai_cleanup_config,
    log_ai_stats, log_ai_detail, free_comfyui_memory,
    register_comfy_prompt, unregister_comfy_prompt, stop_flag_set,
    prompt_vanished,
)

PAUSE_MARKER = '\r\n[pause:3000]'
HEADER_LINES = 4          # title, byline-or-blank, blank, blank

SYSTEM_PROMPT = """You prepare web articles to be read aloud as a podcast. The text was scraped or pasted from a web page, so it can still contain page furniture that is not part of the article.

You are shown the article as numbered lines (long lines are shortened with "..."). Decide which lines are NOT article content and should be deleted, and check the article's metadata.

DELETE lines that are page furniture, such as:
- photo and image captions, photo credits, "Image 1 of 4", gallery or video labels
- newsletter / subscription / sign-up boxes, app promos, ads, "Advertisement"
- share, follow, copy-link, comment-count and reading-time lines
- related-story or "read more" teasers, link lists, tag lists
- an author's name or bio repeated inside the text (the byline), dates or section labels standing alone
- a pull quote that simply repeats a sentence that also appears in the body

KEEP everything else: every sentence of reporting, analysis, opinion, quotes and narrative, and section headings that belong to the article. When unsure, KEEP the line. Never delete more than a small share of the article.

METADATA: the detected values can be wrong or swapped. For example the "title" may really be the article's first paragraph while the true headline sits in the author field, or the author may be a website name. Return the correct values: title is the article's short headline (never a whole paragraph and never the site name; if the current title is a paragraph and a headline-like value exists in another field, use that); author is the person's name (or names), or "" if none is given anywhere; site is the publication or website name.

Reply with ONLY a JSON object, no commentary:
{"remove": [line numbers], "title": "...", "author": "...", "site": "..."}"""


# ------------------------------------------------------------
# Article text <-> numbered lines
# ------------------------------------------------------------
def split_article(text):
    """Returns (header_lines, body_lines, pause_suffix). Header is the
    title/byline block; body_lines keeps blank lines so they can be
    written back in place."""
    pause = ''
    if text.endswith(PAUSE_MARKER):
        text  = text[:-len(PAUSE_MARKER)]
        pause = PAUSE_MARKER
    lines = text.split('\r\n')
    return lines[:HEADER_LINES], lines[HEADER_LINES:], pause

def preview(line, max_words):
    words = line.split()
    n     = len(words)
    if n <= max_words:
        return line.strip(), n
    head = ' '.join(words[:max_words - 6])
    tail = ' '.join(words[-6:])
    return f'{head} ... {tail}', n

def build_prompt(meta, body_lines, max_words):
    numbered = []   # (number, index into body_lines)
    out      = []
    for idx, line in enumerate(body_lines):
        if not line.strip():
            continue
        num = len(numbered) + 1
        numbered.append((num, idx))
        text, n = preview(line, max_words)
        out.append(f'[{num}] ({n}w) {text}')
    head = (f'title: {meta.get("title", "")}\n'
            f'author: {meta.get("author", "")}\n'
            f'site: {meta.get("site", "")}\n')
    if meta.get('source_url'):
        head += f'source: {meta["source_url"]}\n'
    prompt = ('CURRENT METADATA\n' + head + '\nARTICLE LINES\n' + '\n'.join(out) +
              '\n\nReturn the JSON object now.')
    return prompt, numbered


# ------------------------------------------------------------
# LLM call through ComfyUI (CLIPLoader -> TextGenerate -> PreviewAny)
# ------------------------------------------------------------
def build_workflow(cfg, prompt):
    """Loads the cleanup workflow (API-format JSON) and fills the
    {{prompt}} and {{system_prompt}} placeholders. Everything else, such
    as the text model, lives in the file."""
    path = os.path.join(APP_DIR, cfg['workflow_file'])
    if not os.path.isfile(path):          # fresh clone: fall back to the shipped sample
        path = path.replace('.json', '.sample.json')
    with open(path, 'r', encoding='utf-8') as f:
        wf = json.load(f)
    fills = {'{{prompt}}': prompt, '{{system_prompt}}': SYSTEM_PROMPT}
    for node in wf.values():
        for key, val in node.get('inputs', {}).items():
            if isinstance(val, str) and val in fills:
                node['inputs'][key] = fills[val]
    return wf

def reply_node_id(wf):
    for node_id, node in wf.items():
        if node.get('class_type') == 'PreviewAny':
            return node_id
    return None

def run_llm(cfg, prompt):
    """Returns (text, error). Polls ComfyUI history until the prompt ends."""
    base = get_comfy_url()
    if stop_flag_set():
        return None, 'stop requested'
    try:
        wf = build_workflow(cfg, prompt)
    except Exception as e:
        return None, f'could not load {cfg["workflow_file"]}: {e}'
    reply_id = reply_node_id(wf)
    if not reply_id:
        return None, f'{cfg["workflow_file"]} has no PreviewAny node to read the reply from'
    r = requests.post(f'{base}/prompt', json={'prompt': wf}, timeout=30)
    if r.status_code != 200:
        return None, f'ComfyUI rejected the workflow: {r.text[:600]}'
    prompt_id = r.json().get('prompt_id')
    if not prompt_id:
        return None, 'no prompt_id returned'
    register_comfy_prompt(prompt_id)
    try:
        deadline = time.time() + int(cfg['timeout_seconds'])
        missing  = 0
        while time.time() < deadline:
            time.sleep(1.5)
            try:
                hist = requests.get(f'{base}/history/{prompt_id}', timeout=10).json()
            except Exception:
                continue
            if prompt_id in hist:
                entry  = hist[prompt_id]
                status = entry.get('status', {})
                if status.get('status_str') == 'error':
                    msgs = '; '.join(str(m) for m in status.get('messages', []))
                    return None, f'ComfyUI error: {msgs[:800]}'
                if status.get('completed'):
                    texts = (entry.get('outputs', {}).get(reply_id, {}) or {}).get('text')
                    if texts:
                        return texts[0], ''
                    return None, 'workflow finished without text output'
            else:
                missing = missing + 1 if prompt_vanished(prompt_id, base) else 0
                if missing >= 2:
                    return None, 'cancelled'
        return None, f'timed out after {cfg["timeout_seconds"]}s'
    finally:
        unregister_comfy_prompt(prompt_id)

def parse_llm_json(text):
    """Pulls the first JSON object out of the model's reply."""
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


# ------------------------------------------------------------
# Decisions
# ------------------------------------------------------------
def norm(s):
    return re.sub(r'\s+', ' ', (s or '').replace('’', "'")).strip().lower()

def choose_removals(data, numbered, body_lines, max_fraction):
    """Validate the model's line numbers. Returns (indices_to_remove,
    refusal_reason_or_empty)."""
    raw = data.get('remove', [])
    if not isinstance(raw, list):
        return [], 'remove was not a list'
    valid = {num: idx for num, idx in numbered}
    picked = sorted({valid[int(n)] for n in raw
                     if str(n).lstrip('-').isdigit() and int(n) in valid})
    total_words = sum(len(l.split()) for l in body_lines) or 1
    gone_words  = sum(len(body_lines[i].split()) for i in picked)
    # Line count is a loose sanity bound (junk lines are short, and articles with
    # a video or photo gallery are junk-dense);
    # the word share below is the real guard, since junk lines are short.
    if len(picked) > max(8, 2 * max_fraction * len(numbered)):
        return [], f'would remove {len(picked)} of {len(numbered)} lines'
    if gone_words / total_words > max_fraction:
        return [], f'would remove {gone_words}/{total_words} words'
    return picked, ''

def choose_metadata(data, meta):
    """Returns {field: new_value} for fields the model wants changed and
    that pass sanity checks."""
    changes = {}
    title = str(data.get('title') or '').strip()
    if title and norm(title) != norm(meta['title']) and len(title) <= 220:
        changes['title'] = title
    if 'author' in data and data['author'] is not None:
        author = str(data['author']).strip()
        if norm(author) != norm(meta['author']) and len(author) <= 100:
            changes['author'] = author
    site = str(data.get('site') or '').strip()
    if site and norm(site) != norm(meta['site']) and len(site) <= 100:
        changes['site'] = site
    return changes


def main(slug):
    cfg  = get_ai_cleanup_config()
    mode = cfg['mode']
    if mode == 'off':
        return 0

    temp      = get_temp_folder()
    txt_path  = os.path.join(temp, f'{slug}.txt')
    json_path = os.path.join(temp, f'{slug}.json')
    if not os.path.isfile(txt_path):
        print(f'  AI cleanup: no text file for {slug}, skipping.')
        return 0

    started = time.time()
    with open(txt_path, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    sidecar = {}
    if os.path.isfile(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            sidecar = json.load(f)

    header, body_lines, pause = split_article(text)
    meta = {
        'title':      header[0].strip() if header else sidecar.get('title', ''),
        'author':     sidecar.get('artist', ''),
        'site':       sidecar.get('album', ''),
        'source_url': sidecar.get('source_url', ''),
    }
    prompt, numbered = build_prompt(meta, body_lines, int(cfg['max_preview_words']))
    words_before = sum(len(l.split()) for l in body_lines)

    print(f'  AI cleanup ({mode}): {len(numbered)} lines, {words_before} words...')
    llm_start = time.time()
    reply, error = run_llm(cfg, prompt)
    llm_seconds = time.time() - llm_start
    free_comfyui_memory()    # hand the VRAM back before VibeVoice loads

    row = {
        'timestamp':       time.strftime('%Y-%m-%d %H:%M:%S'),
        'slug':            slug,
        'mode':            mode,
        'status':          'ok',
        'llm_seconds':     f'{llm_seconds:.1f}',
        'total_seconds':   '',
        'lines':           len(numbered),
        'words_before':    words_before,
        'words_after':     words_before,
        'lines_removed':   0,
        'words_removed':   0,
        'metadata_fields': '',
        'metadata_action': 'none',
        'note':            '',
    }
    detail = {'slug': slug, 'mode': mode, 'metadata_before': meta, 'reply': reply}

    data = parse_llm_json(reply) if reply else None
    if not data:
        row['status'] = 'failed'
        row['note']   = (error or 'reply was not valid JSON')[:200]
        print(f'  AI cleanup failed ({row["note"]}); article left as-is.')
        detail['error'] = error or 'unparseable reply'
    else:
        picked, refused = choose_removals(
            data, numbered, body_lines, float(cfg['max_remove_fraction']))
        if refused and not stop_flag_set():
            # The model sometimes answers with nearly every line. Ask once
            # more, telling it how few lines are normally page furniture.
            cap = max(3, len(numbered) // 5)
            strict = prompt.replace(
                'Return the JSON object now.',
                f'IMPORTANT: your previous answer listed far too many lines. Of these {len(numbered)} '
                f'lines, normally only 0 to {cap} are page furniture; all the rest are article content '
                f'and must stay. List ONLY lines you are certain are page furniture (captions, photo '
                f'credits, promo boxes, repeated bylines).' + chr(10)*2 + 'Return the JSON object now.')
            print(f'  AI cleanup: reply refused ({refused}); asking again more strictly...')
            retry_start = time.time()
            reply2, error2 = run_llm(cfg, strict)
            llm_seconds += time.time() - retry_start
            row['llm_seconds'] = f'{llm_seconds:.1f}'
            detail['first_reply'] = reply
            detail['reply'] = reply2
            data2 = parse_llm_json(reply2) if reply2 else None
            if data2:
                data = data2
                picked, refused = choose_removals(
                    data, numbered, body_lines, float(cfg['max_remove_fraction']))
                row['note'] = 'retried'
        if refused:
            row['status'] = 'refused'
            row['note']   = refused
            print(f'  AI cleanup removals refused: {refused}')
        removed_text = [body_lines[i] for i in picked]
        detail['removed'] = removed_text

        changes = {}
        # A reply refused for deleting too much is not trusted for metadata either.
        if cfg.get('fix_metadata', True) and not refused:
            changes = choose_metadata(data, meta)
        edited = bool(sidecar.get('metadata_edited'))
        if changes and edited:
            row['metadata_action'] = 'skipped_user_edit'
        elif changes:
            row['metadata_action'] = 'applied' if mode == 'apply' else 'log_only'
        row['metadata_fields'] = ','.join(changes)
        detail['metadata_proposed'] = changes

        row['lines_removed'] = len(picked)
        row['words_removed'] = sum(len(t.split()) for t in removed_text)
        row['words_after']   = words_before - row['words_removed'] if mode == 'apply' else words_before

        if mode == 'apply':
            # keep a copy of the pre-cleanup text for comparison
            # (only the first time, so a re-run never overwrites the original)
            pre_path = os.path.join(temp, f'{slug}.pre-ai.txt')
            if not os.path.isfile(pre_path):
                with open(pre_path, 'w', encoding='utf-8', newline='') as f:
                    f.write(text)
            drop = set(picked)
            body_lines = [l for i, l in enumerate(body_lines) if i not in drop]

            if changes and not edited:
                if 'title' in changes:
                    header[0] = changes['title']
                if 'author' in changes:
                    header[1] = f'Written by {changes["author"]}' if changes['author'] else ''
                new_sidecar = dict(sidecar)
                if 'title' in changes:
                    new_sidecar['title'] = changes['title']
                if 'author' in changes:
                    new_sidecar['artist'] = changes['author']
                if 'site' in changes:
                    new_sidecar['album'] = changes['site']
                new_sidecar['metadata_ai_fixed'] = True
                new_sidecar['metadata_ai_original'] = {
                    'title': meta['title'], 'artist': meta['author'], 'album': meta['site']}
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(new_sidecar, f, indent=2, ensure_ascii=False)

            with open(txt_path, 'w', encoding='utf-8', newline='') as f:
                f.write('\r\n'.join(header + body_lines) + pause)
            print(f'  AI cleanup: removed {len(picked)} line(s)'
                  + (f', fixed {",".join(changes)}' if changes and not edited else '')
                  + f' ({llm_seconds:.0f}s).')
        else:
            print(f'  AI cleanup (log only): would remove {len(picked)} line(s)'
                  + (f', fix {",".join(changes)}' if changes else '') + '.')

    row['total_seconds'] = f'{time.time() - started:.1f}'
    log_ai_stats('ai-cleanup-stats.csv', row)
    log_ai_detail('ai-cleanup-details.jsonl', detail)
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python ai_cleanup.py <slug>')
        sys.exit(1)
    try:
        sys.exit(main(sys.argv[1]))
    except Exception as e:      # never fail the pipeline over an optional step
        print(f'  AI cleanup error (article left as-is): {e}')
        sys.exit(0)
