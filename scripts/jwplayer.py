# jwplayer.py
# Helpers for pages whose main content is an embedded JW Player video
# (e.g. KTAR's /watch/ pages). Used by fetch-article.py (detection + metadata)
# and fetch-jwplayer.py (audio download).

import re, json
import requests

from utils import CHROME_UA

JW_API = 'https://cdn.jwplayer.com/v2/media/{}'

_ID_PATTERNS = [
    r'cdn\.jwplayer\.com/v2/media/([A-Za-z0-9]{8})',
    r'cdn\.jwplayer\.com/(?:thumbs|previews|manifests|videos)/([A-Za-z0-9]{8})',
    r'content\.jwplatform\.com/(?:players|previews|feeds|manifests|videos|thumbs)/([A-Za-z0-9]{8})',
    r'jwplatform\.com/(?:players|previews)/([A-Za-z0-9]{8})',
    r'["\']media_?id["\']\s*[:=]\s*["\']([A-Za-z0-9]{8})["\']',
    r'["\']mediaId["\']\s*[:=]\s*["\']([A-Za-z0-9]{8})["\']',
]

# JSON-LD types that mean "this page is a written story", even if it has a video
_ARTICLE_TYPES = {'NewsArticle', 'Article', 'ReportageNewsArticle',
                  'AnalysisNewsArticle', 'OpinionNewsArticle', 'BlogPosting'}

# How many candidate IDs to look up when matching against the page title.
# Related-video rails can list dozens; the primary video is near the top.
_MAX_CANDIDATES = 6


def find_ids(html):
    """JW Platform media IDs in page order, de-duplicated."""
    seen = []
    for pat in _ID_PATTERNS:
        for m in re.finditer(pat, html):
            if m.group(1) not in seen:
                seen.append(m.group(1))
    return sorted(seen, key=lambda i: html.find(i))


def get_media(media_id):
    """Fetch JW's media JSON. Returns dict or None."""
    try:
        r = requests.get(JW_API.format(media_id),
                         headers={'User-Agent': CHROME_UA}, timeout=15)
        r.raise_for_status()
        data = r.json()
        item = (data.get('playlist') or [{}])[0]
        return {
            'id':          media_id,
            'title':       item.get('title') or data.get('title') or media_id,
            'description': item.get('description') or '',
            'duration':    item.get('duration'),
            'image':       item.get('image'),
            'sources':     item.get('sources', []),
        }
    except Exception as e:
        print(f'  JW Player lookup failed for {media_id}: {e}')
        return None


def pick_source(sources):
    """Audio-only rendition if offered, else the smallest MP4, else HLS."""
    audio = [s for s in sources if str(s.get('type', '')).startswith('audio/')
             and s.get('file')]
    mp4   = [s for s in sources if s.get('type') == 'video/mp4' and s.get('file')]
    hls   = [s for s in sources if 'mpegurl' in str(s.get('type', '')).lower()
             and s.get('file')]
    if audio:
        return audio[0]
    if mp4:
        return sorted(mp4, key=lambda s: s.get('height') or 9999)[0]
    if hls:
        return hls[0]
    return None


def _norm(t):
    return re.sub(r'[^a-z0-9 ]', '', (t or '').lower()).strip()


def _titles_match(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    return len(ta & tb) / max(len(ta | tb), 1) >= 0.8


def _jsonld_types(soup):
    types = set()
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or '')
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if not isinstance(d, dict):
                continue
            t = d.get('@type')
            if isinstance(t, str):
                types.add(t)
            elif isinstance(t, list):
                types.update(x for x in t if isinstance(x, str))
            stack.extend(x for x in d.get('@graph', []) if isinstance(x, dict))
            for k in ('mainEntity', 'mainEntityOfPage', 'video', 'associatedMedia'):
                v = d.get(k)
                if isinstance(v, dict):
                    stack.append(v)
                elif isinstance(v, list):
                    stack.extend(x for x in v if isinstance(x, dict))
    return types


def _meta(soup, **attrs):
    t = soup.find('meta', attrs=attrs)
    return t.get('content') if t and t.get('content') else None


def detect_video_page(html, soup):
    """If this page IS a video page (not an article that happens to embed
    one), return its primary video's media dict, else None.

    A video page: JSON-LD declares a VideoObject and no article type, and a
    JW Player video whose title matches the page title is embedded.
    Articles with a sidebar video, homepages and listing pages return None.
    """
    types = _jsonld_types(soup)
    if 'VideoObject' not in types or types & _ARTICLE_TYPES:
        return None

    ids = find_ids(html)
    if not ids:
        return None

    h1 = soup.find('h1')
    ref = (_meta(soup, property='og:title')
           or (h1.get_text(' ', strip=True) if h1 else None)
           or (soup.title.get_text(strip=True) if soup.title else None))

    first = None
    for mid in ids[:_MAX_CANDIDATES]:
        media = get_media(mid)
        if first is None:
            first = media
        if media and _titles_match(media['title'], ref):
            return media
    # A page with exactly one video and a video-only schema is that video
    if len(ids) == 1:
        return first
    return None
