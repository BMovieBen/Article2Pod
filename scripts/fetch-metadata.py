# fetch-metadata.py
# Usage: python fetch-metadata.py <url>

import os, sys, re, json
import requests
from PIL import ImageDraw
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
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9'
}

def fetch_and_resize_image(img_url, size=(500, 500)):
    try:
        r = requests.get(img_url, headers=HEADERS, timeout=10)
        
        # Explicitly check for HTTP errors before passing to Pillow
        if r.status_code != 200:
            print(f"  [Debug] HTTP {r.status_code}: Server blocked or image not found for {img_url}")
            return None
            
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
    except Exception as e:
        print(f"  [Debug] Fetch error: {e}")
        return None

def create_local_fallback_image(title, size=(500, 500)):
    """Generates a simple, purely local colored square to prevent pipeline crashes."""
    import random
    
    # A palette of vibrant podcast-style background colors (RGB format)
    colors = [(26, 188, 156), (52, 152, 219), (155, 89, 182), (230, 126, 34), (231, 76, 60), (52, 73, 94)]
    bg_color = random.choice(colors)
    
    # Generate the solid color image
    img = Image.new('RGB', size, color=bg_color)
    draw = ImageDraw.Draw(img)
    
    # Grab the first letter of the title for the center
    initial = title[0].upper() if title and len(title) > 0 else "A"
    
    # We use the default font so it doesn't crash searching for .ttf files on your hard drive.
    # It will be small, but it guarantees you have a valid image for your audio encoder!
    draw.text((245, 245), initial, fill=(255, 255, 255))
    
    return img

def search_image(query):
    import time
    
    # 1. Filter out common stop words that confuse image search algorithms
    stop_words = {'a', 'an', 'and', 'the', 'is', 'not', 'in', 'of', 'to', 'for', 'on', 'with', 'by', 'at', 'from', 'as', 'it'}
    important_words = [w for w in query.split() if w.lower() not in stop_words]
    
    # 2. Build a progressive list of queries to try
    queries = [
        query,                                             # Attempt 1: Full title
        ' '.join(query.split()[:5]),                       # Attempt 2: Exactly the first 5 words
        ' '.join(important_words[:4]),                     # Attempt 3: First 4 "important" words
        important_words[0] if important_words else query   # Attempt 4: Hail Mary - Just the main subject (e.g., "Cuba")
    ]
    
    # Remove any duplicates in case the fallbacks generate the exact same string
    queries = list(dict.fromkeys(queries))

    for q in queries:
        print(f"  [Debug] Searching DDG for: '{q}'")
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(q, max_results=5))
            if results:
                for result in results:
                    img = fetch_and_resize_image(result['image'])
                    if img:
                        return img
        except Exception as e:
             print(f"  [Debug] DDG Search error: {e}")
             
        time.sleep(1)  # brief pause between attempts to avoid rate limiting
        
    return None

def get_article_image(url, soup, title=''):
    """Try OG image first, fall back to search, then favicon, then generated text image."""

    # 1. Open Graph image — best quality, article-specific
    og = soup.find('meta', property='og:image')
    if og and og.get('content'):
        img = fetch_and_resize_image(og['content'])
        if img:
            return img

    # 2. Image search using sanitized article title
    if title:
        clean_title = title.split(' | ')[0].split(' - ')[0].split(' — ')[0].strip()
        print(f'  Art: searching for "{clean_title[:50]}"')
        img = search_image(clean_title)
        if img:
            print(f'  Art: found via image search')
            return img

    # 3. Google Favicon (Skips automatically in Clipboard mode)
    domain = urlparse(url).netloc.replace('www.', '') if url else ""
    if domain:
        img = fetch_and_resize_image(f'https://www.google.com/s2/favicons?sz=128&domain={domain}')
        if img:
            print(f'  Art: using Google Favicon for {domain}')
            return img

    # 4. Ultimate Fallback: Local Generated Image (Zero Internet Required)
    print('  Art: All network methods failed. Generating local fallback image.')
    
    img = create_local_fallback_image(title)
    if img:
        print('  Art: Local fallback generated successfully.')
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
    # Skip for YouTube — metadata handled by fetch-youtube.py
    import glob
    youtube_handoffs = glob.glob(os.path.join(TEMP_FOLDER, 'youtube-handoff-*.json'))
    for hf in youtube_handoffs:
        with open(hf, 'r', encoding='utf-8') as f:
            hdata = json.load(f)
        if hdata.get('source_url') == url:
            slug = hdata.get('slug')
            print(f'  YouTube URL — metadata will be fetched during audio download.')
            print(f'SLUG:{slug}')
            return slug
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
    # --- ISOLATED TESTING BLOCKS ---
    if len(sys.argv) >= 3 and sys.argv[1] == '--test-search':
        query = sys.argv[2]
        print(f"Testing search for: '{query}'")
        img = search_image(query)
        if img:
            print("Success! Image found.")
            img.show()
        else:
            print("Failure: No image returned.")
        sys.exit(0)
        
    elif len(sys.argv) >= 3 and sys.argv[1] == '--test-fetch':
        img_url = sys.argv[2]
        print(f"Testing direct fetch for: '{img_url}'")
        img = fetch_and_resize_image(img_url)
        if img:
            print("Success! Image downloaded and resized.")
            img.show()
        else:
            print("Failure: Image function returned None.")
        sys.exit(0)
    # ----------------------------------

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