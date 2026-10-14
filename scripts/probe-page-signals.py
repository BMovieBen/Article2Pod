# probe-page-signals.py
# Gathers the signals needed to decide "article vs. video page" for URLs.
# Usage: python probe-page-signals.py [--chrome] <url> [<url> ...]
#   --chrome  send a full Chrome user agent instead of the one in config.json
# Also writes everything to ..\temp\page-signals.txt

import os, re, sys, json, io
import requests
from bs4 import BeautifulSoup
from readability import Document
from utils import get_user_agent as _cfg_ua

CHROME_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
USE_CHROME = '--chrome' in sys.argv
def get_user_agent():
    return CHROME_UA if USE_CHROME else _cfg_ua()

OUT = []
def say(s=''):
    print(s); OUT.append(s)

ID_PATTERNS = [
    r'cdn\.jwplayer\.com/v2/media/([A-Za-z0-9]{8})',
    r'cdn\.jwplayer\.com/(?:thumbs|previews|manifests|videos)/([A-Za-z0-9]{8})',
    r'content\.jwplatform\.com/(?:players|previews|feeds|manifests|videos|thumbs)/([A-Za-z0-9]{8})',
    r'jwplatform\.com/(?:players|previews)/([A-Za-z0-9]{8})',
    r'["\']media_?id["\']\s*[:=]\s*["\']([A-Za-z0-9]{8})["\']',
    r'["\']mediaId["\']\s*[:=]\s*["\']([A-Za-z0-9]{8})["\']',
]

def jw_ids(html):
    seen = []
    for pat in ID_PATTERNS:
        for m in re.finditer(pat, html):
            if m.group(1) not in seen:
                seen.append(m.group(1))
    return seen

def jw_title(mid):
    try:
        j = requests.get(f'https://cdn.jwplayer.com/v2/media/{mid}',
                         headers={'User-Agent': get_user_agent()}, timeout=15).json()
        it = (j.get('playlist') or [{}])[0]
        return it.get('title'), it.get('duration')
    except Exception as e:
        return f'(lookup failed: {e})', None

def norm(t):
    return re.sub(r'[^a-z0-9 ]', '', (t or '').lower()).strip()

def jsonld_types(soup):
    types = []
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or '')
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                t = d.get('@type')
                if t:
                    types.append(t if isinstance(t, str) else '/'.join(t))
                stack.extend(d.get('@graph', []))
                for k in ('mainEntity', 'mainEntityOfPage', 'video', 'associatedMedia'):
                    if isinstance(d.get(k), (dict, list)):
                        v = d[k]
                        stack.extend(v if isinstance(v, list) else [v])
    return types

def meta(soup, **attrs):
    t = soup.find('meta', attrs=attrs)
    return t.get('content') if t and t.get('content') else None

def probe(url):
    say('=' * 78)
    say(f'URL: {url}')
    try:
        r = requests.get(url, headers={'User-Agent': get_user_agent()}, timeout=15)
    except Exception as e:
        say(f'  FETCH FAILED: {type(e).__name__}: {e}'); return
    say(f'  HTTP {r.status_code}, {len(r.text)} chars, final URL: {r.url}')
    if r.status_code >= 400 or len(r.text) < 200:
        say('  -> looks blocked/empty; body starts: ' + r.text[:200].replace('\n', ' ')); return

    soup = BeautifulSoup(r.text, 'html.parser')
    title_tag = soup.title.get_text(strip=True) if soup.title else None
    h1s = [h.get_text(' ', strip=True) for h in soup.find_all('h1')][:3]
    say(f'  <title>:        {title_tag}')
    say(f'  <h1>:           {h1s}')
    say(f'  og:type:        {meta(soup, property="og:type")}')
    say(f'  og:title:       {meta(soup, property="og:title")}')
    say(f'  og:video:       {meta(soup, property="og:video") or meta(soup, property="og:video:url")}')
    say(f'  twitter:card:   {meta(soup, name="twitter:card")}')
    say(f'  twitter:player: {meta(soup, name="twitter:player")}')
    say(f'  JSON-LD types:  {jsonld_types(soup)}')
    say(f'  <video> tags:   {len(soup.find_all("video"))}   <iframe> tags: {len(soup.find_all("iframe"))}')

    # Same extraction as fetch-article.py (simplified)
    try:
        doc = Document(r.text)
        s2 = BeautifulSoup(doc.summary(), 'html.parser')
        paras = [p.get_text(' ', strip=True) for p in s2.find_all(['p', 'blockquote'])]
        paras = [p for p in paras if len(p) >= 4]
        text = '\n'.join(paras)
        say(f'  readability:    {len(paras)} paragraphs, {len(text)} chars, ~{len(text.split())} words')
        say(f'  text preview:   {text[:200]!r}')
    except Exception as e:
        say(f'  readability failed: {e}')

    ids = jw_ids(r.text)
    say(f'  JW IDs:         {len(ids)} found')
    ref = norm(meta(soup, property='og:title') or (h1s[0] if h1s else title_tag))
    for i, mid in enumerate(ids[:6]):
        t, dur = jw_title(mid)
        match = 'MATCH' if t and ref and (norm(t) in ref or ref in norm(t)) else '     '
        say(f'    [{i}] {mid}  {match}  dur={dur}  {t}')
    if len(ids) > 6:
        say(f'    ... {len(ids) - 6} more not looked up')
    # where does the first ID appear in the HTML? (position hint: main content vs sidebar)
    if ids:
        pos = r.text.find(ids[0])
        say(f'  first JW ID at char {pos} of {len(r.text)} ({100*pos//len(r.text)}% into page)')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    urls = [a for a in sys.argv[1:] if not a.startswith('--')]
    say(f'User agent: {get_user_agent()}')
    for u in urls:
        probe(u)
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'temp', 'page-signals.txt')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'a', encoding='utf-8') as f:
        f.write('\n'.join(OUT) + '\n\n')
    print(f'\nSaved: {out_path}')
