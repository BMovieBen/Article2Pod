"""YouTube: the audio is downloaded with yt-dlp, nothing is read aloud."""
import json, subprocess
from urllib.parse import urlparse
from . import NeedsPaste, Source, length


class YouTube(Source):
    name = 'youtube'

    def matches(self, url, page):
        domain = urlparse(url).netloc.replace('www.', '').replace('m.', '', 1)
        return any(domain == d or domain.endswith('.' + d) for d in ('youtube.com', 'youtu.be'))

    def fetch(self, url, page):
        try:
            out = subprocess.run(['yt-dlp', '--dump-json', '--no-playlist', url],
                                 capture_output=True, text=True, timeout=90)
        except FileNotFoundError:
            raise NeedsPaste('yt-dlp is not installed (pip install yt-dlp).')
        except subprocess.TimeoutExpired:
            raise NeedsPaste('YouTube did not answer in time.')
        if out.returncode != 0:
            raise NeedsPaste(f'yt-dlp could not read that video: {out.stderr.strip()[-300:]}')
        meta    = json.loads(out.stdout)
        channel = meta.get('channel') or meta.get('uploader') or 'Unknown Channel'
        return {'kind': 'youtube', 'title': meta.get('title') or 'Untitled', 'author': channel,
                'site': meta.get('playlist_title') or channel, 'image_url': meta.get('thumbnail'),
                'download': {'how': 'ytdlp', 'url': url},
                'note': f'YouTube video ({length(meta.get("duration"))}): the audio will be downloaded.'}
