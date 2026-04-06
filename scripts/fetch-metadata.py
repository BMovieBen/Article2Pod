# fetch-metadata.py
# Usage: python fetch-metadata.py <url>

import os, sys, re, json
import requests
from readability import Document
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
from urllib.parse import urljoin, urlparse
from ddgs import DDGS
from utils import safe_slug, get_title, get_author, get_site_name, get_temp_folder, get_input_folder

INPUT_FOLDER = get_input_folder()
TEMP_FOLDER  = get_temp_folder()

os.makedirs(INPUT_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)

from utils import safe_slug, get_title, get_author, get_site_name, get_temp_folder, get_input_folder, get_user_agent
HEADERS = {'User-Agent': get_user_agent()}

def fetch_and_resize_image(img_url, size=(500, 500)):
    try:
        r = requests.get(img_url, headers=HEADERS, timeout=10)
        img = Image.open(BytesIO(r.content)).convert('RGB')

        target_w, target_h = size
        orig_w, orig_h = img.size

        scale    = max(target_w / orig_w, target_h / orig_h)
        scaled_w = int(orig_w * scale)
        scaled_h = int(orig_h * scale)
        img      = img.resize((scaled_w, scaled_h), Image.LANCZOS)

        left = (scaled_w - target_w) // 2
        top  = (scaled_h - target_h) // 2
        img  = img.crop((left, top, left + target_w, top + target_h))

        return img
    except Exception:
        return None

def search_image(query):
    """Search for an image using DuckDuckGo."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=5))
        for result in results:
            img = fetch_and_resize_image(result['image'])
            if img:
                return img
    except Exception:
        pass
    return None

def get_article_image(url, soup, title=''):
    """Try OG image first, fall back to image search using article title."""

    # 1. Open Graph image — best quality, article-specific
    og = soup.find('meta', property='og:image')
    if og and og.get('content'):
        img = fetch_and_resize_image(og['content'])
        if img:
            return img

    # 2. Image search using article title
    if title:
        print(f'  Art: searching for "{title[:50]}"')
        img = search_image(title)
        if img:
            print(f'  Art: found via image search')
            return img

    # 3. Clearbit logo as last resort
    domain = urlparse(url).netloc.replace('www.', '')
    img = fetch_and_resize_image(f'https://logo.clearbit.com/{domain}')
    if img:
        print(f'  Art: using Clearbit logo for {domain}')
        return img

    return None

def find_embedded_audio(soup, url):
    """Look for an embedded MP3 URL in the page."""
    # 1. JSON-LD contentUrl
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

    # 2. <audio> tag
    for tag in soup.find_all('audio'):
        src = tag.get('src')
        if src and '.mp3' in src:
            return src
        for source in tag.find_all('source'):
            src = source.get('src')
            if src and '.mp3' in src:
                return src

    # 3. Any link ending in .mp3
    for tag in soup.find_all('a', href=True):
        if '.mp3' in tag['href']:
            return tag['href']

    return None

def fetch_metadata(url):
    # Check for clipboard handoff from fetch-article (blocked site fallback)
    handoff_path     = os.path.join(INPUT_FOLDER, 'clipboard-handoff.json')
    clipboard_author = None
    clipboard_site   = None
    clipboard_title  = None
    clipboard_slug   = None
    if os.path.isfile(handoff_path):
        with open(handoff_path, 'r', encoding='utf-8') as f:
            handoff = json.load(f)
        clipboard_author = handoff.get('clipboard_author')
        clipboard_site   = handoff.get('clipboard_site')
        clipboard_title  = handoff.get('clipboard_title')
        clipboard_slug   = handoff.get('clipboard_slug')
        os.remove(handoff_path)

    # Initialize full_soup — only populated for non-blocked sites
    full_soup = BeautifulSoup('', 'html.parser')

    if clipboard_title:
        title     = clipboard_title
        slug      = clipboard_slug
        author    = clipboard_author
        site_name = clipboard_site
    else:
        r         = requests.get(url, headers=HEADERS, timeout=15)
        full_soup = BeautifulSoup(r.text, 'html.parser')
        doc       = Document(r.text)
        title     = get_title(full_soup, doc)
        slug      = safe_slug(title)
        author    = get_author(full_soup)
        site_name = get_site_name(full_soup, url)

        # Check for embedded audio
        audio_url = find_embedded_audio(full_soup, url)
        if audio_url:
            audio_handoff_path = os.path.join(TEMP_FOLDER, f'audio-handoff-{slug}.json')
            with open(audio_handoff_path, 'w', encoding='utf-8') as f:
                json.dump({'has_audio': True, 'source_url': url, 'audio_url': audio_url}, f)
            print(f'  Audio:      embedded MP3 found, will download directly.')

    # Album art
    art_path = None
    img = get_article_image(url, full_soup, title=title)
    if img:
        art_path = os.path.join(TEMP_FOLDER, f'{slug}.jpg')
        img.save(art_path, 'JPEG', quality=90)
        print(f'  Art saved:  {art_path}')
    else:
        print('  Art:        not found, skipping')

    # JSON sidecar
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

    #print(f'SLUG:{slug}')
    return slug

if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--clipboard':
        url = ''
    elif len(sys.argv) >= 2:
        url = sys.argv[1]
    else:
        print('Usage: python fetch-metadata.py <url>')
        sys.exit(1)
    try:
        fetch_metadata(url if url != '--clipboard' else '')
    except Exception as e:
        print(f'Error: {e}')
        sys.exit(1)