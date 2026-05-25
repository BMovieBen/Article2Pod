# utils.py
# Shared utilities for the Article to Podcast pipeline

import re, json, os
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from readability import Document

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR     = os.path.dirname(SCRIPTS_DIR)
CONFIG_FILE = os.path.join(APP_DIR, 'config.json')

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

def get_input_folder():    return get_required('input_folder')
def get_audio_folder():    return get_required('audio_folder')
def get_temp_folder():     return os.path.join(APP_DIR, 'temp')
def get_output_folder():   return get_required('output_folder')
def get_comfy_url():       return get_required('comfy_url')
def get_workflow_file():
    return os.path.join(APP_DIR, get_required('workflow_file'))
def get_podcasts_folder(): return get_required('podcasts_folder')
def get_track_log():
    return os.path.join(APP_DIR, get_required('track_log'))
def get_user_agent():      return get_required('user_agent')
def get_ad_strip_markers(): return load_config().get('ad_strip_markers', [])
def get_audio_output_prefix(): return get_required('audio_output_prefix')
def get_web_port():        return load_config().get('web_port', 8080)

def safe_slug(title, max_len=50):
    s = title.lower()
    s = re.sub(r'\s+', '-', s)
    s = re.sub(r'[^a-z0-9\-]', '', s)
    s = re.sub(r'-{2,}', '-', s)
    return s.strip('-')[:max_len]

def sanitize_filename(name):
    """Remove characters invalid in Windows filenames."""
    return ''.join(c for c in name.strip() if c not in r'\/:*?"<>|')

def clean_author(text):
    if text.startswith('http') or '/' in text:
        return 'Unknown Author'
    text = re.sub(r'^[Bb][Yy]\s*', '', text).strip()
    text = re.sub(r'\s+(reported\s+from|reporting\s+from|in\s+[A-Z][a-z]+).+$', '', text).strip()
    return re.sub(r'  +', ' ', text)

def get_title(soup, doc):
    # 1. Try <h1> — but skip short ones that are likely site names/logos
    h1 = soup.find('h1')
    if h1:
        text = re.sub(r'  +', ' ', h1.get_text(separator=' ', strip=True))
        if text and len(text) > 15 and ' ' in text:
            return text

    # 2. og:title — strip site name suffix
    og = soup.find('meta', property='og:title')
    if og and og.get('content', '').strip():
        title = og['content'].strip()
        title = re.sub(r'\s*[\:\|]\s*.{3,40}$', '', title).strip()
        if title:
            return title

    # 3. Fallback to readability
    return doc.short_title() or 'Untitled'

def get_author(soup):
    for tag in soup.find_all('a', href=True):
        if any(p in tag['href'] for p in ['/authors/', '/author/']):
            text = tag.get_text(strip=True)
            if text:
                return clean_author(text)
    for attr, val in [('name', 'author'), ('name', 'byl')]:
        tag = soup.find('meta', attrs={attr: val})
        if tag and tag.get('content', '').strip():
            return clean_author(tag['content'].strip())
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
    for selector in ['a[rel="author"]', '.author', '.byline',
                     '[class*="author"]', '[class*="byline"]']:
        tag = soup.select_one(selector)
        if tag:
            text = tag.get_text(strip=True)
            if text and not text.startswith('http'):
                return clean_author(text)
    return 'Unknown Author'

def get_site_name(soup, base_url):
    og = soup.find('meta', property='og:site_name')
    if og and og.get('content', '').strip():
        return og['content'].strip()
    return urlparse(base_url).netloc.replace('www.', '')

def apply_phonetic_replacements(text):
    for phrase, phonetic in load_config().get('phonetic_replacements', {}).items():
        text = text.replace(phrase, phonetic)
    return text

def is_clipboard_domain(url):
    domains = load_config().get('clipboard_domains', [])
    domain  = urlparse(url).netloc.replace('www.', '')
    return any(domain == d or domain.endswith('.' + d) for d in domains)

def is_youtube_url(url):
    domain = urlparse(url).netloc.replace('www.', '')
    return any(domain == d or domain.endswith('.' + d)
               for d in ['youtube.com', 'youtu.be'])

def fetch_and_resize_image(img_url, size=(500, 500)):
    """Download and resize/crop image to square. Returns PIL Image or None."""
    import requests
    from PIL import Image
    from io import BytesIO
    try:
        r = requests.get(img_url,
                         headers={'User-Agent': get_user_agent()},
                         timeout=10)
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
    except Exception:
        return None

JUNK_PATTERNS = [
    re.compile(r'^\s*copyright\s',                               re.IGNORECASE),
    re.compile(r'all rights reserved',                           re.IGNORECASE),
    re.compile(r'may not be published',                          re.IGNORECASE),
    re.compile(r'sign up for',                                   re.IGNORECASE),
    re.compile(r'sign in to your',                               re.IGNORECASE),
    re.compile(r'newsletter',                                    re.IGNORECASE),
    re.compile(r'subscribe',                                     re.IGNORECASE),
    re.compile(r'follow us on',                                  re.IGNORECASE),
    re.compile(r'share this article',                            re.IGNORECASE),
    re.compile(r'read more',                                     re.IGNORECASE),
    re.compile(r'related articles?',                             re.IGNORECASE),
    re.compile(r'^\s*tags?:\s*',                                 re.IGNORECASE),
    re.compile(r'^\s*topics?:\s*',                               re.IGNORECASE),
    re.compile(r'https?://',                                     re.IGNORECASE),
    re.compile(r'^\s*[@#]\w+'),
    re.compile(r'^\s*\d+\s+comments?'),
    re.compile(r'^image\s*:',                                    re.IGNORECASE),
    re.compile(r'^photo\s*:',                                    re.IGNORECASE),
    re.compile(r'^video\s*:',                                    re.IGNORECASE),
    re.compile(r'^published\s+\w+\s+\d+,?\s+\d{4}',             re.IGNORECASE),
    re.compile(r'^\w+\s+\d+,\s+\d{4},?\s+\d+:\d+\s+[AP]M',     re.IGNORECASE),
    re.compile(r'^updated\s+\w+\s+\d+',                         re.IGNORECASE),
    re.compile(r'^hide caption$',                                re.IGNORECASE),
    re.compile(r'^toggle caption$',                              re.IGNORECASE),
    re.compile(r'^\w[\w\s]+\s+for\s+(NPR|AP|Reuters|Getty|AFP)$', re.IGNORECASE),
]

READING_TIME_RE = re.compile(
    r'^~?\d+[\u2013\-]\d+\s+minutes?$'
    r'|^~?\d+\s+minutes?$'
    r'|^\d+\s+min\s+read$'
    r'|^~?\d+\s+min$',
    re.IGNORECASE
)

def parse_reader_mode(text):
    """
    Parse reader mode pasted text.
    Returns (site, title, author, body).
    """

    # Normalize line endings and smart quotes
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace('\u2018', "'").replace('\u2019', "'")  # smart single quotes
    text = text.replace('\u201c', '"').replace('\u201d', '"')  # smart double quotes
    text = text.replace('\u2013', '-').replace('\u2014', '-')  # em/en dashes
    # ... rest of function unchanged

    # Normalize line endings
    text   = text.replace('\r\n', '\n').replace('\r', '\n')
    lines  = text.splitlines()
    nonempty = [l.strip() for l in lines if l.strip()]

    # Find reading time marker — everything before it is header
    header_lines     = []
    reading_time_idx = None
    for i, line in enumerate(nonempty):
        if READING_TIME_RE.match(line):
            reading_time_idx = i
            break
        header_lines.append(line)

    site   = header_lines[0] if len(header_lines) > 0 else ''
    title  = header_lines[1] if len(header_lines) > 1 else 'Untitled'
    author = header_lines[2] if len(header_lines) > 2 else 'Unknown Author'
    author = clean_author(author)

    # Body: everything after reading time, or after first 3 header lines
    if reading_time_idx is not None:
        body_source = nonempty[reading_time_idx + 1:]
    else:
        body_source = nonempty[min(3, len(nonempty)):]

    title_norm = title.strip().lower()
    body_lines = []
    for line in body_source:
        if line.lower() == title_norm:
            continue
        if any(p.search(line) for p in JUNK_PATTERNS):
            continue
        body_lines.append(line)

    body = '\n'.join(body_lines)
    body = re.sub(r'\n{3,}', '\n\n', body).strip()

    return site, title, author, body

def get_queue_file():
    return os.path.join(APP_DIR, load_config().get('queue_file', 'queue.json'))