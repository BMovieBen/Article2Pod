# utils.py
# Shared utilities for the Article to Podcast pipeline

import re, json, os
from bs4 import BeautifulSoup
from readability import Document

SCRIPTS_DIR  = os.path.dirname(os.path.abspath(__file__))
APP_DIR      = os.path.dirname(SCRIPTS_DIR)
CONFIG_FILE  = os.path.join(APP_DIR, 'config.json')

def load_config():
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def get_required(key):
    config = load_config()
    value  = config.get(key)
    if value is None:
        raise KeyError(f'Missing required config key: "{key}". Please check config.json.')
    return value

def get_input_folder():
    return get_required('input_folder')

def get_audio_folder():
    return get_required('audio_folder')

def get_temp_folder():
    return os.path.join(APP_DIR, 'temp')

def get_output_folder():
    return get_required('output_folder')

def get_comfy_url():
    return get_required('comfy_url')

def get_comfy_port():
    from urllib.parse import urlparse
    return str(urlparse(get_required('comfy_url')).port)

def get_workflow_file():
    filename = get_required('workflow_file')
    return os.path.join(APP_DIR, filename)

def get_podcasts_folder():
    return get_required('podcasts_folder')

def get_track_log():
    filename = get_required('track_log')
    return os.path.join(APP_DIR, filename)

def get_user_agent():
    return get_required('user_agent')

def get_ad_strip_markers():
    return load_config().get('ad_strip_markers', [])

def get_audio_output_prefix():
    return get_required('audio_output_prefix')

def safe_slug(title, max_len=50):
    s = title.lower()
    s = re.sub(r'\s+', '-', s)
    s = re.sub(r'[^a-z0-9\-]', '', s)
    return s[:max_len]

def clean_author(text):
    # Reject URLs entirely
    if text.startswith('http') or '/' in text:
        return 'Unknown Author'
    # Strip leading "By" with or without space
    text = re.sub(r'^[Bb][Yy]\s*', '', text).strip()
    # Strip location suffixes
    text = re.sub(r'\s+(reported\s+from|reporting\s+from|in\s+[A-Z][a-z]+).+$', '', text).strip()
    # Normalize multiple spaces
    text = re.sub(r'  +', ' ', text)
    return text

def get_title(soup, doc):
    # 1. Try <h1> first — most reliable for actual article title
    h1 = soup.find('h1')
    if h1:
        text = h1.get_text(strip=True)
        if text and len(text) > 5:
            return text

    # 2. og:title meta tag — strip site name suffix
    og = soup.find('meta', property='og:title')
    if og and og.get('content', '').strip():
        title = og['content'].strip()
        title = re.sub(r'\s*[\:\|]\s*.{3,40}$', '', title).strip()
        if title:
            return title

    # 3. Fallback to readability
    return doc.short_title() or 'Untitled'

def get_author(soup):
    # 0. Author link pattern (/authors/ or /author/ only)
    for tag in soup.find_all('a', href=True):
        if any(p in tag['href'] for p in ['/authors/', '/author/']):
            text = tag.get_text(strip=True)
            if text:
                return clean_author(text)

    # 1. Meta tags
    for attr, val in [
        ('name',     'author'),
        ('name',     'byl'),
    ]:
        tag = soup.find('meta', attrs={attr: val})
        if tag and tag.get('content', '').strip():
            return clean_author(tag['content'].strip())

    # 2. JSON-LD
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data  = json.loads(script.string or '')
            items = data if isinstance(data, list) else [data]
            for item in items:
                author = item.get('author')
                if isinstance(author, dict):
                    name = author.get('name', '').strip()
                    if name: return clean_author(name)
                elif isinstance(author, list) and author:
                    name = author[0].get('name', '').strip()
                    if name: return clean_author(name)
                elif isinstance(author, str) and author.strip():
                    return clean_author(author.strip())
        except Exception:
            pass

# 3. rel="author" or byline CSS patterns
    for selector in ['a[rel="author"]', '.author', '.byline',
                     '[class*="author"]', '[class*="byline"]']:
        tag = soup.select_one(selector)
        if tag:
            text = tag.get_text(strip=True)
            if text and not text.startswith('http'):
                return clean_author(text)

    return 'Unknown Author'

def get_site_name(soup, base_url):
    from urllib.parse import urlparse
    og = soup.find('meta', property='og:site_name')
    if og and og.get('content', '').strip():
        return og['content'].strip()
    return urlparse(base_url).netloc.replace('www.', '')