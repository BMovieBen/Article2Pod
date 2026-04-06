# fetch-audio.py
# Downloads an embedded MP3 directly from a URL (e.g. NPR)
# Usage: python fetch-audio.py <url> <slug>

import os, sys, json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from utils import safe_slug, clean_author, get_title, get_author, load_config, get_comfy_url, get_workflow_file, get_input_folder, get_audio_folder, get_temp_folder, get_user_agent, get_ad_strip_markers

AUDIO_FOLDER = get_audio_folder()
TEMP_FOLDER   = get_temp_folder()
HEADERS = {'User-Agent': get_user_agent()}

def find_audio_url(soup, base_url):
    # 1. JSON-LD contentUrl
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data  = json.loads(script.string or '')
            items = data if isinstance(data, list) else [data]
            for item in items:
                url = item.get('contentUrl') or item.get('url', '')
                if url and url.endswith('.mp3'):
                    return url
                audio = item.get('audio', {})
                if isinstance(audio, dict):
                    url = audio.get('contentUrl') or audio.get('url', '')
                    if url and '.mp3' in url:
                        return url
        except Exception:
            pass

    # 2. <audio> tag with src or nested <source>
    for tag in soup.find_all('audio'):
        src = tag.get('src')
        if src and '.mp3' in src:
            return urljoin(base_url, src)
        for source in tag.find_all('source'):
            src = source.get('src')
            if src and '.mp3' in src:
                return urljoin(base_url, src)

    # 3. Any link ending in .mp3
    for tag in soup.find_all('a', href=True):
        if '.mp3' in tag['href']:
            return urljoin(base_url, tag['href'])

    return None

def download_audio(audio_url, slug):
    print(f'  Downloading: {audio_url}')
    r = requests.get(audio_url, headers=HEADERS, stream=True, timeout=60)
    r.raise_for_status()

    dest     = os.path.join(AUDIO_FOLDER, f'{slug}.mp3')
    total    = int(r.headers.get('content-length', 0))
    received = 0

    with open(dest, 'wb') as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                received += len(chunk)
                if total:
                    pct = int(received / total * 100)
                    print(f'\r  Downloading... {pct}%', end='', flush=True)

    print(f'\r  Downloaded:  {dest}          ')
    return dest

def fetch_audio(url, slug):
    # Check if audio URL was already found during metadata fetch
    handoff_path = os.path.join(TEMP_FOLDER, f'audio-handoff-{slug}.json')
    if os.path.isfile(handoff_path):
        with open(handoff_path, 'r', encoding='utf-8') as f:
            handoff = json.load(f)
        audio_url = handoff.get('audio_url')
        if audio_url:
            download_audio(audio_url, slug)
            print(f'  Audio ready: {slug}.mp3')
            return

    # Fallback: fetch and parse the page
    r    = requests.get(url, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(r.text, 'html.parser')

    audio_url = find_audio_url(soup, url)
    if not audio_url:
        print(f'  No embedded audio found at this URL.')
        sys.exit(1)

    download_audio(audio_url, slug)
    print(f'  Audio ready: {slug}.mp3')

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python fetch-audio.py <url> <slug>')
        sys.exit(1)
    try:
        fetch_audio(sys.argv[1], sys.argv[2])
    except Exception as e:
        print(f'Error: {e}')
        sys.exit(1)