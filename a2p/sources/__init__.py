"""Sources are plug-ins that turn a URL into something to queue.

Each one answers "is this URL mine?" and then fetches it, returning either
an article to be read aloud or audio to download. To add a site-specific
source, write a module here with a Source subclass and add it to ORDER
before WebPage, which is the fallback for any ordinary web page.

A fetched item is a dict:
    kind       'article', or a download kind ('youtube', 'jwplayer', 'audio')
    title, author, site
    text       article body (kind 'article' only): one paragraph per line,
               block quotes indented four spaces
    image_url  cover image to download, or None
    download   how to get the audio (download kinds only); see downloads.py
    note       optional line for the job log
"""
from bs4 import BeautifulSoup
from .. import textclean


def length(seconds):
    """'about 12 min' / 'under a minute' for a log line."""
    minutes = int((seconds or 0) // 60)
    return f'about {minutes} min' if minutes else 'under a minute'


class NeedsPaste(Exception):
    """The page can't be fetched: the user should paste the text instead."""


class Page:
    """A web page, downloaded once and shared by the sources that inspect it."""

    def __init__(self, url):
        self.url, self._html, self._soup = url, None, None

    @property
    def html(self):
        if self._html is None:
            try:
                self._html = textclean.http_get(self.url, timeout=15).text or ''
            except Exception:
                raise NeedsPaste('Connection failed; the site may be blocking bots.')
        return self._html

    @property
    def soup(self):
        if self._soup is None:
            self._soup = BeautifulSoup(self.html, 'html.parser')
        return self._soup


class Source:
    name       = ''
    needs_page = False      # True: matches() looks at the downloaded page, not just the URL

    def matches(self, url, page):
        raise NotImplementedError

    def fetch(self, url, page):
        raise NotImplementedError


def _order():
    from .youtube import YouTube
    from .steam import SteamNews
    from .jwplayer import JWPlayer
    from .audio import EmbeddedAudio
    from .webpage import WebPage
    return [YouTube(), SteamNews(), JWPlayer(), EmbeddedAudio(), WebPage()]


ORDER = None


def fetch(url):
    """Fetch a URL with the first source that claims it. Raises NeedsPaste."""
    global ORDER
    if ORDER is None:
        ORDER = _order()
    from .. import settings
    page = Page(url)
    for source in ORDER:
        if source.needs_page and settings.site_entry(url).get('paste_only'):
            raise NeedsPaste('This site blocks scraping. Paste the text from Reader Mode instead.')
        if source.matches(url, page):
            item = source.fetch(url, page)
            item['source'] = source.name
            return item
    raise NeedsPaste('Nothing usable was found at that address.')
