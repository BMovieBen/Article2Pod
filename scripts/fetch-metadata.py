# fetch-metadata.py
# Usage: python fetch-metadata.py <url>

import os, sys, json, glob, random, time
import requests
import re
from readability import Document
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
from urllib.parse import urlparse
from ddgs import DDGS
from utils import (
    safe_slug, get_title, get_author, get_site_name,
    get_temp_folder, get_input_folder, APP_DIR
)

INPUT_FOLDER = get_input_folder()
TEMP_FOLDER  = get_temp_folder()

os.makedirs(INPUT_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER,  exist_ok=True)

# Richer headers for image fetching — improves success rate vs minimal UA
IMAGE_HEADERS = {
    'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept':          'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Standard headers for page fetching
PAGE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
}

def fetch_and_resize_image(img_url, size=(500, 500)):
    """Download, validate, and resize/crop an image to a square. Returns PIL Image or None."""
    try:
        r = requests.get(img_url, headers=IMAGE_HEADERS, timeout=10)
        if r.status_code != 200:
            print(f'  HTTP {r.status_code}: blocked or not found for {img_url}')
            return None
        img = Image.open(BytesIO(r.content)).convert('RGB')
        target_w, target_h = size
        orig_w, orig_h     = img.size
        scale    = max(target_w / orig_w, target_h / orig_h)
        scaled_w = int(orig_w * scale)
        scaled_h = int(orig_h * scale)
        img      = img.resize((scaled_w, scaled_h), Image.LANCZOS)
        left = (scaled_w - target_w) // 2
        top  = (scaled_h - target_h) // 2
        return img.crop((left, top, left + target_w, top + target_h))
    except Exception as e:
        print(f'  Fetch error: {e}')
        return None

def search_image(query):
    """Search DuckDuckGo images — single attempt, fail fast."""
    clean_query = ' '.join(query.split()[:5])  # first 5 words only
    print(f'  Searching DDG for: \'{clean_query}\'')
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(clean_query, max_results=5))
        if results:
            for result in results:
                img = fetch_and_resize_image(result['image'])
                if img:
                    return img
    except Exception as e:
        print(f'  DDG search error: {e}')
    return None

def get_article_image(url, soup, title='', site_hint=''):
    """
    Try to get album art in order:
    1. OG image tag
    2. DuckDuckGo image search on title
    3. Google Favicon
    4. Default art fallback
    """
    # 1. OG image
    og = soup.find('meta', property='og:image')
    if og and og.get('content'):
        img = fetch_and_resize_image(og['content'])
        if img:
            return img

    # 2. Image search — strip site name suffix from title for cleaner query
    if title:
        clean_title = title.split(' | ')[0].split(' - ')[0].split(' — ')[0].strip()
        print(f'  Art: searching for "{clean_title[:50]}"')
        img = search_image(clean_title)
        if img:
            print('  Art: found via image search')
            return img

    # 3. Google Favicon — use url domain or site_hint from clipboard
    domain = urlparse(url).netloc.replace('www.', '') if url else ''
    if not domain and site_hint:
        # Strip any path/protocol from site_hint — it may be raw like "ktla.com"
        domain = re.sub(r'^https?://', '', site_hint).split('/')[0].replace('www.', '').strip()
    if domain:
        img = fetch_and_resize_image(
            f'https://www.google.com/s2/favicons?sz=128&domain={domain}')
        if img:
            print(f'  Art: using Google Favicon for {domain}')
            return img

    # 4. Default art fallback
    default_art = os.path.join(APP_DIR, 'default_art.jpg')
    if os.path.isfile(default_art):
        print('  Art: using default_art.jpg')
        try:
            from PIL import Image
            return Image.open(default_art).convert('RGB')
        except Exception as e:
            print(f'  Art: failed to load default_art.jpg: {e}')
    return None

def find_embedded_audio(soup, url):
    """Look for an embedded MP3 URL in the page DOM."""
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data  = json.loads(script.string or '')
            items = data if isinstance(data, list) else [data]
            for item in items:
                audio = item.get('audio', {})
                if isinstance(audio, dict):
                    src = audio.get('contentUrl') or audio.get('url', '')
                    if src and '.mp3' in src:
                        return src
                content_url = item.get('contentUrl', '')
                if content_url and '.mp3' in content_url:
                    return content_url
        except Exception:
            pass
    for tag in soup.find_all('audio'):
        src = tag.get('src')
        if src and '.mp3' in src:
            return src
        for source in tag.find_all('source'):
            src = source.get('src')
            if src and '.mp3' in src:
                return src
    for tag in soup.find_all('a', href=True):
        if '.mp3' in tag['href']:
            return tag['href']
    return None

def fetch_metadata(url):
    # YouTube — fetch metadata via yt-dlp now instead of waiting for download
    for hf in glob.glob(os.path.join(TEMP_FOLDER, 'youtube-handoff-*.json')):
        with open(hf, 'r', encoding='utf-8') as f:
            hdata = json.load(f)
        if hdata.get('source_url') == url:
            slug = hdata.get('slug')
            print(f'  YouTube URL — fetching metadata via yt-dlp...')

            try:
                import subprocess
                result = subprocess.run(
                    ['yt-dlp', '--dump-json', '--no-playlist', url],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    import json as _json
                    yt    = _json.loads(result.stdout)
                    title    = yt.get('title', slug)
                    channel  = yt.get('channel', yt.get('uploader', 'YouTube'))
                    playlist = yt.get('playlist_title')
                    album    = playlist if playlist else channel
                    thumbnail = yt.get('thumbnail')

                    # Update the minimal sidecar with real metadata
                    meta = {
                        'title':      title,
                        'artist':     channel,
                        'album':      album,
                        'album_art':  None,
                        'slug':       slug,
                        'source_url': url,
                    }

                    # Get thumbnail
                    if thumbnail:
                        img = fetch_and_resize_image(thumbnail)
                        if img:
                            art_path = os.path.join(TEMP_FOLDER, f'{slug}.jpg')
                            img.save(art_path, 'JPEG', quality=90)
                            meta['album_art'] = art_path
                            print(f'  Art saved:  {art_path}')

                    json_path = os.path.join(TEMP_FOLDER, f'{slug}.json')
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(meta, f, indent=2, ensure_ascii=False)

                    print(f'  Title:      {title}')
                    print(f'  Channel:    {channel}')
                    print(f'  Album:      {album}')
                    print(f'  Meta saved: {json_path}')
                    return slug

            except Exception as e:
                print(f'  yt-dlp metadata failed: {e}, using placeholder.')

            # Fallback — return slug with minimal metadata as before
            return slug

    # Clipboard handoff from fetch-article
    handoff_path = os.path.join(INPUT_FOLDER, 'clipboard-handoff.json')
    clipboard    = {}
    if os.path.isfile(handoff_path):
        with open(handoff_path, 'r', encoding='utf-8') as f:
            clipboard = json.load(f)
        os.remove(handoff_path)

    full_soup = BeautifulSoup('', 'html.parser')

    if clipboard.get('clipboard_title'):
        title     = clipboard['clipboard_title']
        slug      = clipboard['clipboard_slug']
        author    = clipboard['clipboard_author']
        site_name = clipboard['clipboard_site']
    else:
        r         = requests.get(url, headers=PAGE_HEADERS, timeout=15)
        full_soup = BeautifulSoup(r.text, 'html.parser')
        doc       = Document(r.text)
        title     = get_title(full_soup, doc)
        slug      = safe_slug(title)
        author    = get_author(full_soup)
        site_name = get_site_name(full_soup, url)

        audio_url = find_embedded_audio(full_soup, url)
        if audio_url:
            with open(os.path.join(TEMP_FOLDER, f'audio-handoff-{slug}.json'),
                      'w', encoding='utf-8') as f:
                json.dump({'has_audio': True, 'source_url': url,
                           'audio_url': audio_url}, f)
            print(f'  Audio:      embedded MP3 found, will download directly.')

    img      = get_article_image(url, full_soup, title=title, site_hint=site_name)
    art_path = os.path.join(TEMP_FOLDER, f'{slug}.jpg')
    img.save(art_path, 'JPEG', quality=90)
    print(f'  Art saved:  {art_path}')

    meta = {
        'title':      title,
        'artist':     author,
        'album':      site_name,
        'album_art':  art_path,
        'slug':       slug,
        'source_url': url,
    }
    json_path = os.path.join(TEMP_FOLDER, f'{slug}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f'  Title:      {title}')
    print(f'  Author:     {author}')
    print(f'  Site:       {site_name}')
    print(f'  Meta saved: {json_path}')
    return slug

if __name__ == '__main__':
    # Diagnostic test modes
    if len(sys.argv) >= 3 and sys.argv[1] == '--test-search':
        query = sys.argv[2]
        print(f"Testing search for: '{query}'")
        img = search_image(query)
        if img:
            print('Success! Image found.')
            img.show()
        else:
            print('Failure: No image returned.')
        sys.exit(0)

    if len(sys.argv) >= 3 and sys.argv[1] == '--test-fetch':
        img_url = sys.argv[2]
        print(f"Testing direct fetch for: '{img_url}'")
        img = fetch_and_resize_image(img_url)
        if img:
            print('Success! Image downloaded and resized.')
            img.show()
        else:
            print('Failure: Image function returned None.')
        sys.exit(0)

    if len(sys.argv) >= 2 and sys.argv[1] == '--clipboard':
        url = ''
    elif len(sys.argv) >= 2:
        url = sys.argv[1]
    else:
        print('Usage: python fetch-metadata.py <url>')
        sys.exit(1)

    try:
        fetch_metadata('' if url == '--clipboard' else url)
    except Exception as e:
        print(f'Error: {e}')
        sys.exit(1)