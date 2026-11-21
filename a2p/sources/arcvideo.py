"""Arc XP video pages (Gray TV stations such as azfamily.com, and other
sites on Arc's "Fusion" front end with the Powa video player).

Arc pages carry the whole story as JSON in `Fusion.globalContent`. When its
`type` is "video" the page IS a video (an article that embeds one has type
"story"), and the JSON lists direct MP4 / HLS addresses, so the audio is
downloaded instead of reading the text aloud.
"""
import json
from ..textclean import get_site_name
from . import Source, length

MARKER = 'Fusion.globalContent='


def global_content(html):
    """The page's Fusion.globalContent object, or None."""
    i = html.find(MARKER)
    if i < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html, i + len(MARKER))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def pick_stream(streams):
    """Smallest MP4 (audio is all we keep), else the first HLS playlist."""
    good = [s for s in streams or [] if isinstance(s, dict) and s.get('url')]
    mp4 = [s for s in good if s.get('stream_type') == 'mp4']
    if mp4:
        return min(mp4, key=lambda s: s.get('filesize') or s.get('bitrate') or 0)['url']
    hls = [s for s in good if s.get('stream_type') == 'ts' or '.m3u8' in s['url']]
    return hls[0]['url'] if hls else None


def video(html):
    """{'title', 'stream', 'image', 'seconds', 'author'} when the page is an
    Arc video, else None."""
    data = global_content(html)
    if not data or data.get('type') != 'video':
        return None
    stream = pick_stream(data.get('streams'))
    if not stream:
        return None
    by = [c.get('name') for c in (data.get('credits') or {}).get('by') or [] if isinstance(c, dict)]
    return {'title': ((data.get('headlines') or {}).get('basic') or '').strip(),
            'stream': stream,
            'image': (data.get('promo_image') or {}).get('url'),
            'seconds': (data.get('duration') or 0) / 1000,          # Arc gives milliseconds
            'author': next((n.strip() for n in by if n and n.strip()), '')}


class ArcVideo(Source):
    name, needs_page = 'arcvideo', True

    def matches(self, url, page):
        page.arc = video(page.html) if MARKER in page.html else None
        return bool(page.arc)

    def fetch(self, url, page):
        v = page.arc
        site = get_site_name(page.soup, url)
        return {'kind': 'video', 'title': v['title'] or site, 'author': v['author'] or site, 'site': site,
                'image_url': v['image'],
                'download': {'how': 'ffmpeg', 'url': v['stream']},
                'note': f'Video page ({length(v["seconds"])}): the audio will be downloaded.'}
