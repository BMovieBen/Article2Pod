"""Text-model clean-up of a fetched article, run once per job before it is read.

The model is shown the article as numbered, shortened lines and answers
with line numbers to delete (leftover page furniture) plus corrected
title / author / site. It never writes article text: the deletions are
done here, a reply that would remove too much is refused (after one
stricter re-ask), and metadata the user edited is never overwritten.
On any failure the article is left exactly as it was.
"""
import re, time
from . import db, llm, settings

SYSTEM_PROMPT = """You prepare web articles to be read aloud as a podcast. The text was scraped or pasted from a web page, so it can still contain page furniture that is not part of the article.

You are shown the article as numbered lines (long lines are shortened with "..."). Decide which lines are NOT article content and should be deleted, and check the article's metadata.

DELETE lines that are page furniture, such as:
- photo and image captions, photo credits, "Image 1 of 4", gallery or video labels
- newsletter / subscription / sign-up boxes, app promos, ads, "Advertisement"
- share, follow, copy-link, comment-count and reading-time lines
- related-story or "read more" teasers, link lists, tag lists
- an author's name or bio repeated inside the text (the byline), dates or section labels standing alone
- a pull quote that simply repeats a sentence that also appears in the body

KEEP everything the writer wrote, however short or informal. Page furniture is what the WEBSITE added around the article; anything in the writer's own voice is article content. In particular KEEP:
- one-line remarks, jokes, exclamations and asides
- the introduction to a recurring segment or feature of a column (for example a weekly look at an archive, a regular "news from the states" round-up, a running joke that opens a section)
- a line that introduces a quotation or says where it comes from, such as "From The New York Times:" or "As the AP reports:"
- the writer's sign-off, dedication or closing thought at the end
- every sentence of reporting, analysis, opinion, quotes and narrative, and section headings that belong to the article
When unsure, KEEP the line. Most articles have no furniture at all or only a few lines of it; an empty list is a normal answer. Never delete more than a small share of the article.

METADATA: the detected values can be wrong, shifted or swapped. Typical mistakes: the "title" is really a sub-headline or the first sentence or paragraph of the article, while the true headline sits in the author or site field; or the author is a website name. Return the correct values:
- title: the article's short headline. A long sentence that ends with a period and describes the story (a sub-headline or standfirst) is NOT the headline. If the current title looks like that, and a headline-like value (short, title-case, no ending period) appears in the author or site field or at the top of the article lines, use that value as the title. Never use a whole paragraph or the site name. If the current title already reads like a headline, return it unchanged: never replace it with the name of a section, series or recurring feature mentioned in the article.
- author: the person's name (or names), or "" if none is given anywhere. Never a headline or a website name.
- site: the publication or website name, never a headline. Use the byline, the source URL or a line such as "Add The New York Times as a preferred source" to identify it.

Reply with ONLY a JSON object, no commentary:
{"remove": [line numbers], "title": "...", "author": "...", "site": "..."}"""


def preview(line, max_words):
    """(shortened line, its real word count)."""
    words = line.split()
    if len(words) <= max_words:
        return line.strip(), len(words)
    return ' '.join(words[:max_words - 6]) + ' ... ' + ' '.join(words[-6:]), len(words)


def build_prompt(meta, lines, max_words=22):
    """lines are the article's body lines; they are numbered from 1."""
    out = []
    for num, line in enumerate(lines, 1):
        text, n = preview(line, max_words)
        out.append(f'[{num}] ({n}w) {text}')
    head = f'title: {meta["title"]}\nauthor: {meta["author"]}\nsite: {meta["site"]}\n'
    if meta.get('url'):
        head += f'source: {meta["url"]}\n'
    return ('CURRENT METADATA\n' + head + '\nARTICLE LINES\n' + '\n'.join(out) +
            '\n\nReturn the JSON object now.')


def strict_prompt(prompt, n_lines):
    """The re-ask used when the first answer listed far too many lines."""
    cap = max(3, n_lines // 5)
    return prompt.replace(
        'Return the JSON object now.',
        f'IMPORTANT: your previous answer listed far too many lines. Of these {n_lines} lines, '
        f'normally only 0 to {cap} are page furniture; all the rest are article content and must '
        f'stay. List ONLY lines you are certain are page furniture (captions, photo credits, promo '
        f'boxes, repeated bylines).\n\nReturn the JSON object now.')


TEASER_RE = re.compile(r'^(read|see|watch|listen|related|more|also|sign up|subscribe|advertis|share|follow|'
                       r'tags?|topics?|photos?|image|video|gallery|credit|source|caption)\b', re.I)


def keep_patterns(site=None):
    """The "keep_lines" entries from the settings plus the site's own."""
    entries = list(settings.load()['keep_lines']) + list((site or {}).get('keep_lines') or [])
    out = []
    for e in entries:
        e = str(e).strip()
        if e:
            out.append(re.compile(e[3:], re.I) if e.startswith('re:') else re.compile(re.escape(e), re.I))
    return out


def protected(line, patterns=()):
    """True for a line clean-up must never delete, whatever the model says:
    one that matches a keep_lines entry, or one that introduces what
    follows (ends with a colon, like "From The Hill:") and is not a
    website teaser such as "Read more:"."""
    text = line.strip()
    if any(p.search(text) for p in patterns):
        return True
    if text.endswith(':') and not TEASER_RE.match(text):
        return True
    return False


def choose_removals(data, lines, max_fraction, patterns=()):
    """Validate the model's line numbers. Returns (indices to remove,
    refusal reason or '', indices the keep rules saved)."""
    raw = data.get('remove', [])
    if not isinstance(raw, list):
        return [], 'remove was not a list', []
    wanted = sorted({int(n) - 1 for n in raw
                     if str(n).isdigit() and 1 <= int(n) <= len(lines)})
    kept   = [i for i in wanted if protected(lines[i], patterns)]
    picked = [i for i in wanted if i not in set(kept)]
    total = sum(len(l.split()) for l in lines) or 1
    gone  = sum(len(lines[i].split()) for i in picked)
    # The line count is only a loose bound (junk lines are short and photo
    # galleries are junk-dense); the share of words is the real guard.
    if len(picked) > max(8, 2 * max_fraction * len(lines)):
        return [], f'would remove {len(picked)} of {len(lines)} lines', kept
    if gone / total > max_fraction:
        return [], f'would remove {gone} of {total} words', kept
    return picked, '', kept


def _norm(s):
    return re.sub(r'\s+', ' ', (s or '').replace('’', "'")).strip().lower()


def looks_wrong(title):
    """A detected title is only replaced when it does not read like a
    headline: empty, a full sentence ending in a period, or far too long.
    A headline that already looks fine is never swapped for something else
    the model found (a section or series name, say)."""
    t = (title or '').strip()
    return (not t or t.lower() in ('untitled', 'article') or len(t.split()) > 28
            or (t.endswith('.') and len(t.split()) > 8))


def choose_metadata(data, meta):
    """{field: new value} for the fields the model wants changed that pass
    sanity checks (title and site cannot be emptied; nothing absurdly long)."""
    changes = {}
    title = str(data.get('title') or '').strip()
    if title and _norm(title) != _norm(meta['title']) and len(title) <= 220 and looks_wrong(meta['title']):
        changes['title'] = title
    if data.get('author') is not None:
        author = str(data['author']).strip()
        if _norm(author) != _norm(meta['author']) and len(author) <= 100:
            changes['author'] = author
    site = str(data.get('site') or '').strip()
    if site and _norm(site) != _norm(meta['site']) and len(site) <= 100:
        changes['site'] = site
    return changes


def decide(meta, lines, ask=None, options=None, site=None):
    """Ask the model and validate. Returns a dict:
       status        ok | failed | refused
       remove        indices of lines to delete
       kept_by_rule  indices the model wanted gone that the keep rules saved
       metadata      {field: new value}
       note, seconds, retried"""
    ask = ask or llm.ask
    opt = options or settings.load()['cleanup_options']
    patterns = keep_patterns(site)
    out = {'status': 'ok', 'remove': [], 'kept_by_rule': [], 'metadata': {}, 'note': '', 'retried': False}
    started = time.time()
    prompt  = build_prompt(meta, lines, int(opt['preview_words']))
    reply, error = ask(prompt, SYSTEM_PROMPT)
    data = llm.parse_json(reply) if reply else None
    if not data:
        out.update(status='failed', note=(error or 'the reply was not valid JSON')[:200])
    else:
        picked, refused, kept = choose_removals(data, lines, float(opt['max_remove_fraction']), patterns)
        if refused:
            # The model sometimes answers with nearly every line: ask once more.
            out['retried'] = True
            reply2, _ = ask(strict_prompt(prompt, len(lines)), SYSTEM_PROMPT)
            data2 = llm.parse_json(reply2) if reply2 else None
            if data2:
                data = data2
                picked, refused, kept = choose_removals(data, lines, float(opt['max_remove_fraction']), patterns)
        if refused:
            out.update(status='refused', note=refused)      # not trusted for metadata either
        else:
            out['remove'], out['kept_by_rule'] = picked, kept
            if opt['fix_metadata']:
                out['metadata'] = choose_metadata(data, meta)
    out['seconds'] = round(time.time() - started, 1)
    return out


def run(job, log):
    """Clean one job in place (once). Returns the refreshed job."""
    if job['info'].get('cleanup') or not job.get('text'):
        return job
    site = settings.site_entry(job.get('url') or '', job.get('site') or '')
    if site.get('cleanup') is False:
        log('Clean-up is turned off for this site.')
        db.merge_info(job['id'], cleanup={'status': 'off for site'})
        return db.get_job(job['id'])
    lines = [l for l in job['text'].split('\n') if l.strip()]
    meta  = {'title': job['title'] or '', 'author': job['author'] or '',
             'site': job['site'] or '', 'url': job.get('url') or ''}
    log(f'Clean-up: {len(lines)} lines, {sum(len(l.split()) for l in lines)} words...')
    d = decide(meta, lines, site=site)
    record = {'status': d['status'], 'seconds': d['seconds'], 'retried': d['retried'], 'note': d['note']}
    if d['status'] != 'ok':
        log(f'Clean-up {d["status"]} ({d["note"]}); article left as it is.')
        if d['status'] == 'failed' and 'Stop requested' in d['note']:
            return job                                  # try again on the next run
    else:
        drop    = set(d['remove'])
        removed = [lines[i] for i in sorted(drop)]
        fields  = {}
        if removed:
            fields['text'] = '\n'.join(l for i, l in enumerate(lines) if i not in drop)
        changes = d['metadata']
        if changes and job['info'].get('metadata_edited'):
            record['metadata_skipped'] = changes        # the user's edits win
            changes = {}
        if changes:
            record['metadata_before'] = {k: meta[k] for k in changes}
            fields.update(changes)
        record['removed'] = removed
        record['kept_by_rule'] = [lines[i] for i in d['kept_by_rule']]
        if fields:
            db.update_job(job['id'], **fields)
        log(f'Clean-up: removed {len(removed)} line(s)'
            + (f', fixed {", ".join(changes)}' if changes else '') + f' ({d["seconds"]:.0f}s).')
        for line in removed:                                # so nothing disappears unseen
            log(f'  removed: {line.strip()[:160]}')
        if d['kept_by_rule']:
            log(f'  kept {len(d["kept_by_rule"])} line(s) the text model wanted to remove (keep rules).')
    db.merge_info(job['id'], cleanup=record)
    return db.get_job(job['id'])
