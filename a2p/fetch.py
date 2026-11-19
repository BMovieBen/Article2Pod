"""Turn a URL or pasted Reader Mode text into a queued job.

URLs go through the source plug-ins (a2p/sources). An article's body is
stored on the job as plain text: one paragraph per line, block quotes
indented four spaces. Title, author and site are separate fields;
pronunciation fixes are applied later, when the text is read, so changing
them never needs a re-fetch.
"""
import os
from urllib.parse import parse_qsl, urlencode, urlsplit
from . import db, settings, sources, textclean
from .sources import NeedsPaste                      # noqa: F401  (re-exported for callers)
from .sources.webpage import clean_body


def from_url(url):
    """The fetched item for a URL (see a2p/sources). Raises NeedsPaste."""
    return sources.fetch(url)


def from_paste(pasted):
    """Reader Mode paste -> an article item."""
    site, title, author, body = textclean.parse_reader_mode(pasted)
    lines = [l.rstrip() for l in body.replace('\r\n', '\n').split('\n')]
    return {'kind': 'article', 'title': title or 'Untitled', 'author': author or 'Unknown Author',
            'site': site or 'Unknown Site', 'image_url': None, 'source': 'paste',
            'text': clean_body('\n'.join(lines), author)}


# ---- duplicates --------------------------------------------------------------
class Duplicate(Exception):
    """The same article is already in the queue."""


class AlreadyMade(Exception):
    """The same article was already made and its file is still in the library."""


TRACKING = {'fbclid', 'gclid', 'mc_cid', 'mc_eid', 'cmpid', 'ref', 'ref_src', 'smid', 'taid', 'ito', 'mod', 'guccounter'}


def url_key(url):
    """A URL reduced to what identifies the page: no scheme, no www., no
    #anchor, no tracking parameters, no trailing slash."""
    u = urlsplit((url or '').strip())
    host = u.netloc.lower().removeprefix('www.')
    query = sorted((k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
                   if not (k.lower().startswith('utm_') or k.lower() in TRACKING))
    return host + (u.path.rstrip('/') or '') + ('?' + urlencode(query) if query else '')


def _item_key(title, site, author):
    return tuple(' '.join((x or '').lower().split()) for x in (title, site, author))


def check_duplicate(url='', item=None, again=False):
    """Raise Duplicate when the article is already queued, or AlreadyMade
    (unless again) when it was made and its file still exists. A URL is
    matched on url_key; pasted text on title, site and author."""
    if url:
        key = url_key(url)
        same = lambda j: j['url'] and url_key(j['url']) == key
    else:
        key = _item_key(item['title'], item['site'], item['author'])
        same = lambda j: _item_key(j['title'], j['site'], j['author']) == key
    for j in db.jobs():
        if not same(j):
            continue
        if j['status'] != 'done':
            raise Duplicate(f'Already in the queue: {j["title"]}')
        if not again and j['output_path'] and os.path.isfile(j['output_path']):
            raise AlreadyMade(f'Already made and still in your library: {j["title"]}. Add it again?')


def add(url='', pasted='', again=False):
    """Fetch and queue one item. Returns the job id. Raises Duplicate,
    AlreadyMade (pass again=True to add anyway) or NeedsPaste."""
    if url:
        check_duplicate(url, again=again)             # before fetching, so a duplicate costs nothing
        item = from_url(url)
    else:
        item = from_paste(pasted)
        check_duplicate(item=item, again=again)
    info = {'source': item.get('source')}
    if item['kind'] == 'article':
        words = len((item.get('text') or '').split())
        if words < 20:
            raise NeedsPaste('Almost no article text was found.')
        info['words'] = words
    else:
        info['download'] = item['download']
    site = settings.site_entry(url, item['site'])
    job_id = db.add_job(
        slug=textclean.safe_slug(item['title']) or 'article', kind=item['kind'], url=url or None,
        title=item['title'], author=item['author'], site=item['site'], text=item.get('text'),
        voice=(site.get('voice') or '') if item['kind'] == 'article' else '',
        engine=(site.get('engine') or '') if item['kind'] == 'article' else '', info=info)
    art, source = _art(job_id, item.get('image_url'), site)
    db.update_job(job_id, art_path=art)
    db.merge_info(job_id, art_source=source)
    db.log(job_id, f'Added: {item["title"]} ({item["site"]}, {item["author"]}'
                   + (f', {info["words"]} words)' if 'words' in info else ')'))
    if item.get('note'):
        db.log(job_id, item['note'])
    return job_id


def job_dir(job_id):
    path = os.path.join(settings.WORK_DIR, str(job_id))
    os.makedirs(path, exist_ok=True)
    return path


def _art(job_id, image_url, site):
    """Cover art for a job: the site's fixed art, else the page's image,
    else default_art.jpg (which art.py may replace with generated art).
    Returns (path or None, source: site | page | default)."""
    fixed = site.get('art')
    if fixed:
        fixed = fixed if os.path.isabs(fixed) else os.path.join(settings.ROOT, fixed)
        if os.path.isfile(fixed):
            return fixed, 'site'
    if image_url:
        try:
            img = textclean.fetch_and_resize_image(image_url)
            if img:
                path = os.path.join(job_dir(job_id), 'art.jpg')
                img.convert('RGB').save(path, 'JPEG', quality=90)
                return path, 'page'
        except Exception:
            pass
    default = os.path.join(settings.ROOT, 'default_art.jpg')
    return (default if os.path.isfile(default) else None), 'default'
