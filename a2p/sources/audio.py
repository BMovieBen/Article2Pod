"""Pages that carry their own audio (an MP3 in the page's metadata, an
<audio> player, or a plain link): the MP3 is downloaded instead of reading
the text aloud."""
import json
from urllib.parse import urljoin
from .. import textclean
from . import Source


def find_mp3(soup, base_url):
    """The page's MP3 address, or None."""
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
        except Exception:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict):
                continue
            audio = item.get('audio')
            for src in ((audio.get('contentUrl'), audio.get('url')) if isinstance(audio, dict) else ()) \
                    + (item.get('contentUrl'),):
                if src and '.mp3' in src:
                    return urljoin(base_url, src)
    for tag in soup.find_all('audio'):
        for src in [tag.get('src')] + [s.get('src') for s in tag.find_all('source')]:
            if src and '.mp3' in src:
                return urljoin(base_url, src)
    for tag in soup.find_all('a', href=True):
        if '.mp3' in tag['href']:
            return urljoin(base_url, tag['href'])
    return None


class EmbeddedAudio(Source):
    name, needs_page = 'audio', True

    def matches(self, url, page):
        page.mp3 = find_mp3(page.soup, url)
        return bool(page.mp3)

    def fetch(self, url, page):
        soup = page.soup

        class _Doc:
            def short_title(self):
                return soup.title.get_text(strip=True) if soup.title else 'Untitled'
        og = soup.find('meta', property='og:image')
        return {'kind': 'audio', 'title': textclean.get_title(soup, _Doc()),
                'author': textclean.get_author(soup) or 'Unknown Author',
                'site': textclean.get_site_name(soup, url),
                'image_url': og.get('content') if og and og.get('content') else None,
                'download': {'how': 'http', 'url': page.mp3},
                'note': 'The page has its own audio: the MP3 will be downloaded.'}
