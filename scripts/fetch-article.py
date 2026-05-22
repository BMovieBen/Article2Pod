# fetch-article.py
# Usage: python fetch-article.py <url> [--web]

import os, sys, re, json
import requests
from readability import Document
from bs4 import BeautifulSoup
from utils import (
    safe_slug, clean_author, get_title, get_author,
    load_config, get_input_folder, get_temp_folder,
    get_user_agent, get_ad_strip_markers, apply_phonetic_replacements,
    is_clipboard_domain, is_youtube_url, parse_reader_mode
)

INPUT_FOLDER = get_input_folder()
TEMP_FOLDER  = get_temp_folder()

BLOCK_INDICATORS = [
    'access to this page has been denied',
    'access denied',
    'please enable cookies',
    'checking your browser',
    'cloudflare',
    'captcha',
    'enable javascript',
    'robot or human',
    'unusual traffic',
]

def is_blocked(title, text):
    combined = (title + ' ' + text).lower()
    return any(phrase in combined for phrase in BLOCK_INDICATORS) and len(text) < 500

def fetch_article(url):
    os.makedirs(TEMP_FOLDER, exist_ok=True)
    os.makedirs(INPUT_FOLDER, exist_ok=True)

    if not url:
        # Direct clipboard/text mode — handoff file already written by caller
        handoff_path = os.path.join(INPUT_FOLDER, 'clipboard-handoff.json')
        if not os.path.isfile(handoff_path):
            print('  No clipboard handoff found.')
            sys.exit(1)
        with open(handoff_path, 'r', encoding='utf-8') as f:
            handoff = json.load(f)
        slug = handoff.get('clipboard_slug')
        # txt file already written by web_pipeline.py — nothing more to do
        print(f'  Slug:     {slug}')
        return slug

    elif is_youtube_url(url):
        print(f'  YouTube URL detected, will download audio directly.')
        from urllib.parse import urlparse, parse_qs
        parsed   = urlparse(url)
        video_id = parsed.path.lstrip('/') if 'youtu.be' in parsed.netloc \
                   else parse_qs(parsed.query).get('v', ['unknown'])[0]
        slug = f'youtube-{video_id}'
        with open(os.path.join(TEMP_FOLDER, f'youtube-handoff-{slug}.json'),
                  'w', encoding='utf-8') as f:
            json.dump({'youtube': True, 'source_url': url, 'slug': slug}, f)
        with open(os.path.join(TEMP_FOLDER, f'{slug}.json'),
                  'w', encoding='utf-8') as f:
            json.dump({'title': slug, 'artist': '', 'album': 'YouTube',
                       'album_art': None, 'slug': slug, 'source_url': url},
                      f, indent=2)
        print(f'  Slug:     {slug}')
        return slug

    elif is_clipboard_domain(url):
        print('  Known unsupported site, switching to text mode.')
        sys.exit(2)

    else:
        headers = {'User-Agent': get_user_agent()}
        try:
            r = requests.get(url, headers=headers, timeout=15)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError):
            print('  Connection failed, site may be blocking bots.')
            sys.exit(2)

        full_soup = BeautifulSoup(r.text, 'html.parser')

        if not r.text or len(r.text.strip()) < 200:
            print('  Page appears empty or JS-rendered.')
            sys.exit(2)

        try:
            doc  = Document(r.text)
            soup = BeautifulSoup(doc.summary(), 'html.parser')

            for tag in soup.find_all(['figcaption', 'figure']):
                tag.decompose()
            for tag in soup.find_all(class_=lambda c: c and any(
                x in c.lower() for x in ['caption', 'credit', 'hide-caption']
            )):
                tag.decompose()

            blocks            = []
            seen_fingerprints = set()
            for tag in soup.find_all(['p', 'blockquote']):
                text_content = tag.get_text(separator=' ', strip=True)
                if not text_content or len(text_content) < 4:
                    continue
                fingerprint = ' '.join(text_content.lower().split()[:8])
                if fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(fingerprint)
                if tag.name == 'blockquote':
                    lines        = text_content.splitlines()
                    text_content = '\r\n'.join(f'    {line}' for line in lines)
                blocks.append(text_content)

            text = '\r\n'.join(blocks)
            text = re.sub(r'(\r\n){3,}', '\r\n\r\n', text)

            for marker in get_ad_strip_markers():
                idx = text.find(marker)
                if idx != -1:
                    text = text[:idx].rstrip()
                    break

            title  = get_title(full_soup, doc)
            author = get_author(full_soup)

            if is_blocked(title, text):
                print('  Site appears to be blocking scraping.')
                sys.exit(2)

            slug = safe_slug(title)

        except SystemExit:
            raise
        except Exception as e:
            print(f'  Readability error: {e}')
            sys.exit(2)

    text = apply_phonetic_replacements(text)
    header   = f'{title}\r\nWritten by {author}\r\n\r\n\r\n'
    txt_path = os.path.join(TEMP_FOLDER, f'{slug}.txt')
    with open(txt_path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write(header + text + '\r\n[pause:3000]')

    print(f'  Title:      {title}')
    print(f'  Author:     {author}')
    print(f'  Slug:       {slug}')
    print(f'  Saved:      {txt_path}')
    return slug

if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--web']
    if '--clipboard' in args:
        url = ''
    elif args:
        url = args[0]
    else:
        print('Usage: python fetch-article.py <url> [--web]')
        sys.exit(1)
    try:
        fetch_article(url)
    except SystemExit:
        raise
    except Exception as e:
        print(f'Error: {e}')
        sys.exit(1)