"""Source plug-ins and download jobs, with the network stubbed out."""
import json
import pytest
from a2p import db, downloads, fetch, runner, settings, sources
from a2p.sources import audio as audio_src, jwplayer, steam, webpage, youtube
from test_core import conf     # noqa: F401  (fixture)

ARTICLE_HTML = '''<html><head><title>Big Story - Example News</title>
<meta property="og:site_name" content="Example News"><meta property="og:image" content="https://x/i.jpg">
<meta name="author" content="Jo Bloggs"></head><body><article><h1>A Big Story About Something Important</h1>
''' + ''.join(f'<p>Paragraph {i} of the story has plenty of real words so that it counts as content.</p>' for i in range(8)) + '''
<blockquote><p>This is a quoted passage from another publication that runs for a while.</p></blockquote>
<p>Paragraph 0 of the story has plenty of real words so that it counts as content.</p>
</article></body></html>'''


class FakePage:
    def __init__(self, html):
        from bs4 import BeautifulSoup
        self.html, self.soup = html, BeautifulSoup(html, 'html.parser')


def use_page(monkeypatch, html):
    monkeypatch.setattr(sources, 'Page', lambda url: FakePage(html))


# ---- which source claims a URL ---------------------------------------------
def test_url_only_matching():
    yt, st = youtube.YouTube(), steam.SteamNews()
    assert yt.matches('https://www.youtube.com/watch?v=abc', None) and yt.matches('https://youtu.be/abc', None)
    assert yt.matches('https://m.youtube.com/watch?v=abc', None)
    assert not yt.matches('https://notyoutube.com/watch', None)
    assert st.matches('https://store.steampowered.com/news/app/123/view/456', None)
    assert not st.matches('https://store.steampowered.com/app/123/', None)

def test_plain_page_goes_to_webpage_source(conf, monkeypatch):
    conf()
    use_page(monkeypatch, ARTICLE_HTML)
    item = sources.fetch('https://example.com/a')
    assert item['source'] == 'webpage' and item['kind'] == 'article'
    assert item['title'] == 'A Big Story About Something Important' and item['author'] == 'Jo Bloggs'
    assert item['site'] == 'Example News' and item['image_url'] == 'https://x/i.jpg'
    lines = item['text'].split('\n')
    assert len(lines) == 9 and lines[-1].startswith('    This is a quoted passage')      # repeat dropped, quote indented

def test_paste_only_site_never_downloads_the_page(conf, monkeypatch):
    conf(sites={'example.com': {'paste_only': True}})
    def boom(url):
        raise AssertionError('page must not be fetched')
    monkeypatch.setattr(sources, 'Page', lambda url: type('P', (), {'html': property(lambda s: boom(url))})())
    with pytest.raises(sources.NeedsPaste):
        sources.fetch('https://www.example.com/a')

def test_block_page_asks_for_paste(conf, monkeypatch):
    conf()
    use_page(monkeypatch, '<html><head><title>Access Denied</title></head><body>' + '<p>You have been blocked.</p>' * 20 + '</body></html>')
    with pytest.raises(sources.NeedsPaste):
        sources.fetch('https://example.com/a')

def test_embedded_audio_page(conf, monkeypatch):
    conf()
    html = ARTICLE_HTML.replace('</article>', '<audio><source src="/media/show.mp3?x=1"></audio></article>')
    use_page(monkeypatch, html)
    item = sources.fetch('https://example.com/a')
    assert item['kind'] == 'audio' and item['download'] == {'how': 'http', 'url': 'https://example.com/media/show.mp3?x=1'}

def test_find_mp3_in_jsonld():
    html = '<script type="application/ld+json">' + json.dumps({'@type': 'PodcastEpisode', 'audio': {'contentUrl': 'https://cdn/x.mp3'}}) + '</script>'
    assert audio_src.find_mp3(FakePage(html).soup, 'https://e.com/') == 'https://cdn/x.mp3'
    assert audio_src.find_mp3(FakePage('<p>nothing</p>').soup, 'https://e.com/') is None


# ---- JW Player ---------------------------------------------------------------
def jw_page(types, ids=('AbCd1234',)):
    ld = json.dumps([{'@type': t} for t in types])
    return (f'<html><head><meta property="og:title" content="City Council Meeting Recap">'
            f'<meta property="og:site_name" content="KTAR.com">'
            f'<script type="application/ld+json">{ld}</script></head><body>'
            + ''.join(f'<div data-src="https://cdn.jwplayer.com/v2/media/{i}"></div>' for i in ids) + '</body></html>')

def test_jwplayer_video_page_vs_article(conf, monkeypatch):
    conf()
    media = {'id': 'AbCd1234', 'title': 'City Council Meeting Recap', 'duration': 600, 'image': 'https://x/t.jpg', 'sources': []}
    monkeypatch.setattr(jwplayer, 'get_media', lambda mid: dict(media, id=mid))
    use_page(monkeypatch, jw_page(['VideoObject']))
    item = sources.fetch('https://ktar.com/watch/x')
    assert item['kind'] == 'jwplayer' and item['download'] == {'how': 'jwplayer', 'media_id': 'AbCd1234'}
    assert item['site'] == 'KTAR.com' and item['author'] == 'KTAR.com'
    # an article that merely embeds a video stays an article
    page = FakePage(jw_page(['VideoObject', 'NewsArticle']))
    assert jwplayer.detect_video_page(page.html, page.soup) is None

def test_jwplayer_source_choice():
    pick = jwplayer.pick_source
    assert pick([{'type': 'video/mp4', 'file': 'a', 'height': 720}, {'type': 'audio/mp4', 'file': 'b'}])['file'] == 'b'
    assert pick([{'type': 'video/mp4', 'file': 'a', 'height': 720}, {'type': 'video/mp4', 'file': 'c', 'height': 180}])['file'] == 'c'
    assert pick([{'type': 'application/vnd.apple.mpegurl', 'file': 'h'}])['file'] == 'h'
    assert pick([]) is None


# ---- Steam -------------------------------------------------------------------
def test_steam_helpers():
    assert steam.app_id('https://store.steampowered.com/news/app/4743930/view/69088') == '4743930'
    assert steam.app_id('https://store.steampowered.com/news/') is None
    html = '<meta property="og:title" content="Guild Wars 3 - Our Design Philosophy - Steam News">'
    assert steam.page_title(html) == 'Our Design Philosophy'
    raw = ('[h1]Heading[/h1]\n\n[img]{STEAM_CLAN_IMAGE}/1/a.png[/img]\nFirst &amp; second [url=https://x]link text[/url].\n'
           '[previewyoutube=abc;full][/previewyoutube]\n[list][*]One point[/list]')
    assert steam.strip_bbcode(raw) == 'Heading\nFirst & second link text.\nOne point'
    items = [{'title': 'Other'}, {'title': 'Our  Design philosophy', 'contents': 'x'}]
    assert steam.pick_item(items, 'Our Design Philosophy') is items[1]
    assert steam.pick_item(items, '') is None


# ---- YouTube -----------------------------------------------------------------
def test_youtube_metadata(monkeypatch):
    meta = {'title': 'A Video', 'channel': 'The Channel', 'playlist_title': None, 'thumbnail': 'https://x/t.jpg', 'duration': 754}
    monkeypatch.setattr(youtube.subprocess, 'run',
                        lambda *a, **k: type('R', (), {'returncode': 0, 'stdout': json.dumps(meta), 'stderr': ''})())
    item = youtube.YouTube().fetch('https://youtu.be/abc', None)
    assert (item['kind'], item['title'], item['author'], item['site']) == ('youtube', 'A Video', 'The Channel', 'The Channel')
    assert item['download'] == {'how': 'ytdlp', 'url': 'https://youtu.be/abc'} and '12 min' in item['note']
    monkeypatch.setattr(youtube.subprocess, 'run',
                        lambda *a, **k: type('R', (), {'returncode': 1, 'stdout': '', 'stderr': 'Video unavailable'})())
    with pytest.raises(sources.NeedsPaste, match='unavailable'):
        youtube.YouTube().fetch('https://youtu.be/abc', None)


# ---- download jobs end to end ---------------------------------------------------
@pytest.fixture
def dl(conf, monkeypatch, tmp_path):
    conf(output_dir=str(tmp_path / 'out'))
    monkeypatch.setattr(settings, 'WORK_DIR', str(tmp_path / 'work'))
    monkeypatch.setattr(fetch, 'from_url', lambda url: {
        'kind': 'youtube', 'source': 'youtube', 'title': 'A Video', 'author': 'The Channel', 'site': 'The Channel',
        'image_url': None, 'download': {'how': 'ytdlp', 'url': url}, 'note': 'YouTube video'})
    monkeypatch.setattr(runner.comfy, 'running', lambda: False)        # downloads must not need ComfyUI
    runner.comfy.stop.clear()
    return tmp_path

def _fake_mp3(dest):
    import subprocess
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono', '-t', '0.3',
                    '-codec:a', 'libmp3lame', dest], check=True)

def test_download_job_runs_without_comfyui(dl, monkeypatch):
    monkeypatch.setattr(downloads, 'ytdlp', lambda url, dest, log: _fake_mp3(dest))
    job_id = fetch.add(url='https://youtu.be/abc')
    job = db.get_job(job_id)
    assert job['kind'] == 'youtube' and job['voice'] == '' and job['text'] is None
    runner.run_queue()
    job = db.get_job(job_id)
    assert job['status'] == 'done' and job['output_path'].endswith('The Channel - A Video.mp3')
    from mutagen.id3 import ID3
    tags = ID3(job['output_path'])
    assert tags['TIT2'].text[0] == 'A Video' and tags['TPE1'].text[0] == 'The Channel' and tags['TRCK'].text[0] == '1'

def test_articles_wait_when_comfyui_is_down(dl, monkeypatch):
    monkeypatch.setattr(downloads, 'ytdlp', lambda url, dest, log: _fake_mp3(dest))
    article = db.add_job(slug='a', kind='article', title='T', author='A', site='S', text='word ' * 50)
    video = fetch.add(url='https://youtu.be/abc')
    runner.run_queue()
    assert db.get_job(video)['status'] == 'done' and db.get_job(article)['status'] == 'pending'

def test_failed_download_is_reported(dl, monkeypatch):
    def broken(url, dest, log):
        raise RuntimeError('yt-dlp failed: Video unavailable')
    monkeypatch.setattr(downloads, 'ytdlp', broken)
    job_id = fetch.add(url='https://youtu.be/abc')
    runner.run_queue()
    job = db.get_job(job_id)
    assert job['status'] == 'failed' and 'unavailable' in job['error']

def test_stop_during_download(dl, monkeypatch):
    def stopped(url, dest, log):
        runner.comfy.stop.set()
        raise downloads.Cancelled()
    monkeypatch.setattr(downloads, 'ytdlp', stopped)
    job_id = fetch.add(url='https://youtu.be/abc')
    runner.run_queue()
    assert db.get_job(job_id)['status'] == 'stopped'
    runner.comfy.stop.clear()


# ---- Arc XP video pages (azfamily.com and other Gray TV stations) -------------
def arc_page(kind, streams):
    content = {'type': kind, 'headlines': {'basic': 'Lights Return to Scottsdale'}, 'duration': 133071,
               'credits': {'by': [{'name': ''}]}, 'promo_image': {'url': 'https://img/x.jpg'}, 'streams': streams}
    return (ARTICLE_HTML.replace('</head>', '<script>Fusion.arcSite="azfamily";Fusion.globalContent='
                                 + json.dumps(content) + ';Fusion.layout="x";</script></head>'))

ARC_STREAMS = [
    {'stream_type': 'ts', 'url': 'https://cdn/x/master.m3u8', 'filesize': 30},
    {'stream_type': 'mp4', 'url': 'https://cdn/x/big.mp4', 'filesize': 90},
    {'stream_type': 'mp4', 'url': 'https://cdn/x/small.mp4', 'filesize': 20},
]

def test_arc_video_page_is_downloaded(conf, monkeypatch):
    conf()
    use_page(monkeypatch, arc_page('video', ARC_STREAMS))
    item = sources.fetch('https://www.azfamily.com/video/2026/11/20/lights/')
    assert item['source'] == 'arcvideo' and item['kind'] == 'video'
    assert item['title'] == 'Lights Return to Scottsdale' and item['image_url'] == 'https://img/x.jpg'
    assert item['download'] == {'how': 'ffmpeg', 'url': 'https://cdn/x/small.mp4'}
    assert item['author'] == item['site'] and 'about 2 min' in item['note']

def test_arc_story_with_a_video_is_still_an_article(conf, monkeypatch):
    conf()
    use_page(monkeypatch, arc_page('story', ARC_STREAMS))
    assert sources.fetch('https://www.azfamily.com/2026/11/20/story/')['kind'] == 'article'

def test_arc_stream_choice():
    from a2p.sources import arcvideo
    assert arcvideo.pick_stream(ARC_STREAMS[:1]) == 'https://cdn/x/master.m3u8'
    assert arcvideo.pick_stream([]) is None
    assert arcvideo.global_content('Fusion.globalContent={broken') is None

def test_site_name_that_is_an_address_becomes_the_domain():
    from bs4 import BeautifulSoup
    from a2p import textclean
    soup = BeautifulSoup('<meta property="og:site_name" content="https://www.azfamily.com">', 'html.parser')
    assert textclean.get_site_name(soup, 'https://www.azfamily.com/video/x/') == 'azfamily'
    soup = BeautifulSoup('<meta property="og:site_name" content="Arizona Family">', 'html.parser')
    assert textclean.get_site_name(soup, 'https://www.azfamily.com/x/') == 'Arizona Family'
