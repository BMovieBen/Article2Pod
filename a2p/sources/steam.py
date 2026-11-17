"""Steam news posts. The store page is rendered by JavaScript, so the text
comes from Steam's news API instead and its BBCode markup is stripped."""
import re
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from . import NeedsPaste, Source
from .webpage import clean_body

API = 'https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid={}&count=20&maxlength=0&format=json'


def app_id(url):
    """The app id from /news/app/<id>/view/<post>, or None."""
    parts = urlparse(url).path.strip('/').split('/')
    try:
        return parts[parts.index('app') + 1]
    except (ValueError, IndexError):
        return None


def page_title(html):
    """The post's title from og:title ("Game - Post Title - Steam News")."""
    og = BeautifulSoup(html, 'html.parser').find('meta', property='og:title')
    title = og['content'].strip() if og and og.get('content') else ''
    for suffix in (' - Steam News', ' - Steam'):
        if title.endswith(suffix):
            title = title[:-len(suffix)].strip()
            break
    return title.split(' - ', 1)[1].strip() if ' - ' in title else title


def strip_bbcode(raw):
    """BBCode and Steam markup -> plain text, one paragraph per line."""
    text = re.sub(r'\{STEAM_CLAN_IMAGE\}/\S+', '', raw)
    text = re.sub(r'\[img[^\]]*\].*?\[/img\]', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[previewyoutube[^\]]*\].*?\[/previewyoutube\]', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[/?\w[^\]]*\]', '', text)                  # remaining tags: keep the inner text
    text = text.replace('[*]', '')                              # list bullets
    for ent, ch in (('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'), ('&nbsp;', ' '), ('&#39;', "'"), ('&quot;', '"')):
        text = text.replace(ent, ch)
    return '\n'.join(line.strip() for line in text.replace('\r', '').split('\n') if line.strip())


def _norm(t):
    return re.sub(r'\s+', ' ', t or '').strip().lower()


def pick_item(items, title):
    """The API item whose title matches the page's (the ids in the store
    URL and in the API are unrelated, so the title is the only link)."""
    return next((i for i in items if title and _norm(i.get('title', '')) == _norm(title)), None)


class SteamNews(Source):
    name = 'steam'

    def matches(self, url, page):
        parsed = urlparse(url)
        return 'steampowered.com' in parsed.netloc and '/news/' in parsed.path

    def fetch(self, url, page):
        appid = app_id(url)
        if not appid:
            raise NeedsPaste('Could not read the app id from that Steam address.')
        try:
            items = requests.get(API.format(appid), timeout=15).json().get('appnews', {}).get('newsitems', [])
            title = page_title(requests.get(url, timeout=15).text)
        except Exception as e:
            raise NeedsPaste(f'Steam did not answer ({e}). Paste the text instead.')
        item = pick_item(items, title)
        text = strip_bbcode(item.get('contents', '')) if item else ''
        if not text:
            raise NeedsPaste('That post is not in Steam\'s news feed. Paste the text instead.')
        author = (item.get('author') or '').strip() or 'Unknown Author'
        return {'kind': 'article', 'title': item.get('title') or title, 'author': author,
                'site': 'Steam', 'text': clean_body(text, author), 'image_url': None}
