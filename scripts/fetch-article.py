# fetch-article.py
# Usage: python fetch-article.py <url>

import os, sys, re, json
import requests
import pyperclip
from readability import Document
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from utils import safe_slug, clean_author, get_title, get_author, load_config, get_input_folder, get_temp_folder, get_user_agent, get_ad_strip_markers, apply_phonetic_replacements

INPUT_FOLDER  = get_input_folder()
TEMP_FOLDER   = get_temp_folder()

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

def is_clipboard_domain(url):
    config  = load_config()
    domains = config.get('clipboard_domains', [])
    domain  = urlparse(url).netloc.replace('www.', '')
    return any(domain == d or domain.endswith('.' + d) for d in domains)

def is_blocked(title, text):
    combined = (title + ' ' + text).lower()
    return any(phrase in combined for phrase in BLOCK_INDICATORS) and len(text) < 500

def clean_reader_mode_text(raw, title=''):
    lines = raw.splitlines()
    cleaned = []
    body_started = False
    reading_time_pattern = re.compile(r'^\d+[\u2013\-]\d+\s+minutes?$', re.IGNORECASE)

    junk_patterns = [
        re.compile(r'^\s*copyright\s', re.IGNORECASE),
        re.compile(r'all rights reserved', re.IGNORECASE),
        re.compile(r'may not be published', re.IGNORECASE),
        re.compile(r'sign up for', re.IGNORECASE),
        re.compile(r'sign in to your', re.IGNORECASE),
        re.compile(r'newsletter', re.IGNORECASE),
        re.compile(r'subscribe', re.IGNORECASE),
        re.compile(r'follow us on', re.IGNORECASE),
        re.compile(r'share this article', re.IGNORECASE),
        re.compile(r'read more', re.IGNORECASE),
        re.compile(r'related articles?', re.IGNORECASE),
        re.compile(r'^\s*tags?:\s*', re.IGNORECASE),
        re.compile(r'^\s*topics?:\s*', re.IGNORECASE),
        re.compile(r'https?://', re.IGNORECASE),
        re.compile(r'^\s*[@#]\w+'),
        re.compile(r'^\s*\d+\s+comments?'),
        re.compile(r'^image\s*:', re.IGNORECASE),
        re.compile(r'^photo\s*:', re.IGNORECASE),
        re.compile(r'^video\s*:', re.IGNORECASE),
        re.compile(r'^published\s+\w+\s+\d+,?\s+\d{4}', re.IGNORECASE),
        re.compile(r'^\w+\s+\d+,\s+\d{4},?\s+\d+:\d+\s+[AP]M', re.IGNORECASE),
        re.compile(r'^updated\s+\w+\s+\d+', re.IGNORECASE),
    ]

    title_normalized = title.strip().lower() if title else ''

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if body_started:
                cleaned.append('')
            continue

        if not body_started:
            if reading_time_pattern.match(stripped):
                body_started = True
            continue

        if title_normalized and stripped.lower() == title_normalized:
            continue

        words = stripped.split()
        if len(words) <= 5 and not stripped.endswith(('.', '?', '!')):
            continue

        if any(p.search(stripped) for p in junk_patterns):
            continue

        cleaned.append(stripped)

    text = '\r\n'.join(cleaned)
    text = re.sub(r'(\r\n){3,}', '\r\n\r\n', text)
    return text.strip()

def extract_reader_mode_meta(raw):
    lines        = [l.strip() for l in raw.splitlines() if l.strip()]
    reading_time = re.compile(r'^\d+[\u2013\-]\d+\s+minutes?$', re.IGNORECASE)
    header_lines = []
    for line in lines:
        if reading_time.match(line):
            break
        header_lines.append(line)
    site   = header_lines[0] if len(header_lines) > 0 else ''
    title  = header_lines[1] if len(header_lines) > 1 else 'Untitled'
    author = header_lines[2] if len(header_lines) > 2 else 'Unknown Author'
    author = clean_author(author)
    return site, title, author

def fetch_from_clipboard(url, forced=False):
    print()
    print('  ╔══════════════════════════════════════════════════════╗')
    if forced:
        print('  ║  Clipboard/reader mode selected.                    ║')
    else:
        print('  ║  This site is blocking automated scraping.          ║')
    print('  ║                                                      ║')
    print('  ║  To continue:                                        ║')
    print('  ║  1. Open the URL in your browser                     ║')
    print('  ║  2. Switch to READER MODE (F9 in Firefox/Edge)       ║')
    print('  ║  3. Select All (Ctrl+A) and Copy (Ctrl+C)            ║')
    print('  ║  4. Come back here and press Enter                   ║')
    print('  ╚══════════════════════════════════════════════════════╝')
    print()
    input('  Press Enter when clipboard is ready...')

    raw = pyperclip.paste()
    if not raw or len(raw) < 100:
        print('  Clipboard appears empty or too short. Aborting.')
        sys.exit(1)

    site, title, author = extract_reader_mode_meta(raw)

    text = clean_reader_mode_text(raw, title=title)
    if not text:
        print('  Could not extract article text from clipboard. Aborting.')
        sys.exit(1)

    slug = safe_slug(title)
    handoff = {
        'clipboard_author': author,
        'clipboard_site':   site,
        'clipboard_title':  title,
        'clipboard_slug':   slug,
    }
    handoff_path = os.path.join(INPUT_FOLDER, 'clipboard-handoff.json')
    with open(handoff_path, 'w', encoding='utf-8') as f:
        json.dump(handoff, f)

    return title, slug, author, text

def fetch_article(url):
    if not url:
        title, slug, author, text = fetch_from_clipboard(url, forced=True)
    else:
        if is_clipboard_domain(url):
            print(f'  Known unsupported site, switching to clipboard mode.')
            title, slug, author, text = fetch_from_clipboard(url, forced=False)
        else:
            headers = {'User-Agent': 'Mozilla/5.0'}
            try:
                r = requests.get(url, headers=headers, timeout=15)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError):
                print(f'  Connection failed, site may be blocking bots.')
                title, slug, author, text = fetch_from_clipboard(url, forced=False)
            else:
                full_soup = BeautifulSoup(r.text, 'html.parser')
                doc       = Document(r.text)
                soup      = BeautifulSoup(doc.summary(), 'html.parser')

                blocks = []
                for tag in soup.find_all(['p', 'blockquote']):
                    text_content = tag.get_text(separator=' ', strip=True)
                    if not text_content:
                        continue
                    if tag.name == 'blockquote':
                        lines        = text_content.splitlines()
                        text_content = '\r\n'.join(f'    {line}' for line in lines)
                    blocks.append(text_content)

                text = '\r\n'.join(blocks)
                text = re.sub(r'(\r\n){3,}', '\r\n\r\n', text)

                for marker in get_ad_strip_markers():
                    ad_index = text.find(marker)
                    if ad_index != -1:
                        text = text[:ad_index].rstrip()
                        break

                title  = get_title(full_soup, doc)
                author = get_author(full_soup)

                if is_blocked(title, text):
                    print(f'  Warning: site appears to be blocking scraping.')
                    title, slug, author, text = fetch_from_clipboard(url, forced=False)
                else:
                    slug = safe_slug(title)

    header = (
        title + '\r\n' +
        f'Written by {author}' + '\r\n\r\n\r\n'
    )

    txt_path = os.path.join(TEMP_FOLDER, f'{slug}.txt')

    from utils import apply_phonetic_replacements
    text = apply_phonetic_replacements(text)

    with open(txt_path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write(header + text)

    print(f'  Title:      {title}')
    print(f'  Author:     {author}')
    print(f'  Slug:       {slug}')
    print(f'  Saved:      {txt_path}')

    return slug

if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--clipboard':
        url = ''
    elif len(sys.argv) >= 2:
        url = sys.argv[1]
    else:
        print('Usage: python fetch-article.py <url> OR python fetch-article.py --clipboard')
        sys.exit(1)
    try:
        fetch_article(url)
    except Exception as e:
        print(f'Error: {e}')
        sys.exit(1)