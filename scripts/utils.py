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
def get_voice_folder():
    folder = load_config().get('voice_folder', os.path.join(APP_DIR, 'voices'))
    os.makedirs(folder, exist_ok=True)
    return folder
def get_comfy_url():       return get_required('comfy_url')
def get_workflow_file():
    return os.path.join(APP_DIR, get_required('workflow_file'))
def get_output_dir():
    return os.path.join(APP_DIR, 'output')
def get_track_log():
    return os.path.join(APP_DIR, get_required('track_log'))
def get_user_agent():      return get_required('user_agent')
def get_ad_strip_markers(): return load_config().get('ad_strip_markers', [])
def get_audio_output_prefix(): return get_required('audio_output_prefix')
def get_web_port():        return load_config().get('web_port', 8080)
def get_generation_logging_enabled():
    return bool(load_config().get('generation_logging_enabled', True))
def get_chunk_word_count():
    return int(load_config().get('chunk_word_count', 1400))
def get_audio_normalize_enabled():
    return bool(load_config().get('audio_normalize_enabled', False))

def normalize_audio(mp3_path):
    """Run ffmpeg loudness normalization on mp3_path in place: dynaudnorm
    followed by loudnorm targeting -16 LUFS (a common target for spoken-word
    / podcast content). Writes to a temp file first and only replaces the
    original on success, so a failed or interrupted run never leaves a
    corrupt or partial file behind. Returns (ok, error_message) -- never
    raises, so the caller can always fall back to the unnormalized file."""
    import subprocess, shutil as _shutil
    if not _shutil.which('ffmpeg'):
        return False, 'ffmpeg not found on PATH.'

    tmp_path = mp3_path + '.normalizing.tmp.mp3'
    result = subprocess.run(
        ['ffmpeg', '-y', '-i', mp3_path,
         '-af', 'dynaudnorm,loudnorm=I=-16:TP=-1.5:LRA=11',
         '-codec:a', 'libmp3lame', '-q:a', '2',
         tmp_path],
        capture_output=True, text=True
    )

    if result.returncode != 0 or not os.path.isfile(tmp_path):
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False, result.stderr.strip()[-500:]

    os.replace(tmp_path, mp3_path)
    return True, ''

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

# TLDs stripped when turning a bare domain into a display-friendly site
# name. Case-sensitive on the remainder — whatever capitalization the
# source used (og:site_name-less scrape, or text pasted from reader mode)
# is preserved as-is rather than forcing title-case, since brand names
# like "NYTimes" or "IGN" don't follow simple capitalization rules.
_DOMAIN_TLD_RE = re.compile(r'\.(com|net|org|co|io|tv|news)$', re.IGNORECASE)

def domain_to_site_name(domain):
    """Turn a bare domain like 'Polygon.com' into a display-friendly site
    name by stripping a trailing TLD, preserving whatever capitalization
    was already present (e.g. 'NYTimes.com' -> 'NYTimes'). Used both as
    the last-resort site-name fallback when scraping (no og:site_name tag)
    and when extracting a site name mentioned in reader-mode pasted text."""
    domain = domain.strip()
    name   = _DOMAIN_TLD_RE.sub('', domain)
    return name or domain

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
    domain = urlparse(base_url).netloc.replace('www.', '')
    return domain_to_site_name(domain)

def resolve_domain(url='', site_hint=''):
    """Best-effort bare domain (e.g. 'esquire.com') from a URL, falling
    back to a site-name hint when no URL is available -- used for URL-mode
    articles where the domain is known outright, and as a secondary signal
    for Text mode where some Reader Mode pastes put a raw domain on the
    site line. Only treats site_hint as a domain if it actually looks like
    one (has a dot, no spaces) -- a display name like 'The Verge' is not
    mistaken for a domain."""
    domain = urlparse(url).netloc.replace('www.', '') if url else ''
    if not domain and site_hint:
        hint = site_hint.strip()
        if '.' in hint and ' ' not in hint:
            domain = re.sub(r'^https?://', '', hint).split('/')[0].replace('www.', '').strip()
    return domain.lower()

def get_domain_overrides():
    return load_config().get('domain_overrides', {})

def get_domain_override(url='', site_hint=''):
    """Return the domain_overrides config entry (dict, possibly containing
    'voice_file' and/or 'art_path') for the given url or site_hint, or
    None if nothing matches. Two independent match strategies, since URL
    mode and Text mode give very different signals:
      - URL mode: url's real domain, matched by suffix (so 'esquire.com'
        in config also matches 'www.esquire.com').
      - Text mode: no URL exists, only whatever site name Reader Mode put
        on the header line (e.g. 'Esquire' or, on some sites, a raw
        domain like 'esquire.com'). Matched against both the configured
        domain as-is and its TLD-stripped display form ('esquire.com' ->
        'esquire'), case-insensitively.
    art_path is resolved relative to APP_DIR if not already absolute."""
    domain    = resolve_domain(url, site_hint)
    hint_name = ''
    if site_hint:
        hint_name = re.sub(r'^https?://', '', site_hint.strip()) \
                      .split('/')[0].replace('www.', '').strip().lower()

    if not domain and not hint_name:
        return None

    for d, cfg in get_domain_overrides().items():
        d_norm = d.lower().replace('www.', '').strip()
        d_bare = domain_to_site_name(d_norm).lower()

        matched = False
        if domain and (domain == d_norm or domain.endswith('.' + d_norm)):
            matched = True
        elif hint_name and (hint_name == d_norm or hint_name == d_bare):
            matched = True

        if matched:
            resolved = dict(cfg)
            art_path = resolved.get('art_path')
            if art_path and not os.path.isabs(art_path):
                resolved['art_path'] = os.path.join(APP_DIR, art_path)
            return resolved
    return None

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
    re.compile(r'^\s*topics?:\s*',                                re.IGNORECASE),
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

# Firefox-on-iOS reader mode (Format B) omits the site name and author lines
# entirely, giving just: Title / "Jul 20, 2026, 2:55 PM" / body. Without
# this, the fixed site/title/author header-line assumption below shifts by
# one and swaps the real title for the date, and the first body paragraph
# for the author.
DATE_LINE_RE = re.compile(
    r'^\w{3,9}\.?\s+\d{1,2},\s+\d{4},?\s+\d{1,2}:\d{2}\s*[AP]\.?M\.?$',
    re.IGNORECASE
)

# Polygon-on-iOS reader mode (Format C) gives: Title / Author / "Follow" /
# "Link copied to clipboard" / a bare vote count / "By" / "Published <date>
# <time> <tz>" / dek lines / caption / body. The trailing timezone and
# "Published" prefix mean this never matches DATE_LINE_RE above, so it
# needs its own boundary marker.
PUBLISHED_LINE_RE = re.compile(
    r'^published\s+\w+\.?\s+\d{1,2},\s+\d{4},?\s+\d{1,2}:\d{2}\s*[AP]\.?M\.?'
    r'(\s+[A-Za-z]{2,5})?$',
    re.IGNORECASE
)

# UI chrome lines that can appear between the author and the Published line
# in Format C — skipped when scanning for the real author line.
HEADER_JUNK_LINE_RE = re.compile(r'^(follow|link copied to clipboard|by)$', re.IGNORECASE)
BYLINE_RE = re.compile(r'^by\s+', re.IGNORECASE)

# Wire-service datelines ("DETROIT (AP) — ...") are a reliable fallback
# for the site name when reader mode gives us no site/source at all (e.g.
# Firefox-on-iOS, Polygon-on-iOS). Only checked when site is otherwise
# unknown.
WIRE_SERVICE_NAMES = {
    'AP':      'Associated Press',
    'REUTERS': 'Reuters',
    'AFP':     'AFP',
    'UPI':     'UPI',
}
WIRE_SERVICE_RE = re.compile(
    r'^[A-Z][A-Za-z.,\'\s]{0,40}\(\s*(AP|Reuters|AFP|UPI)\s*\)\s*[—\-]',
)

# Some sites' iOS reader-mode paste includes a login prompt naming the site
# itself, e.g. "Sign in to your Polygon.com account" -- normally dropped
# entirely by the 'sign in to your' JUNK_PATTERN, but worth extracting as a
# site-name fallback before it gets discarded.
SIGNIN_SITE_RE = re.compile(
    r'sign in to your\s+([A-Za-z0-9][A-Za-z0-9\-]*\.[A-Za-z]{2,})\s+account',
    re.IGNORECASE
)

def detect_wire_service(body_lines, max_lines=3):
    """Look at the first few body lines for a wire-service dateline like
    'DETROIT (AP) — ...' and return the full service name if found,
    else None."""
    for line in body_lines[:max_lines]:
        m = WIRE_SERVICE_RE.match(line)
        if m:
            return WIRE_SERVICE_NAMES.get(m.group(1).upper())
    return None

def detect_signin_site(body_lines, max_lines=10):
    """Look at the first several body lines for a 'Sign in to your
    <site> account' prompt and return a display-friendly site name if
    found, else None. Preserves the capitalization used in the prompt
    itself (e.g. 'Polygon.com' -> 'Polygon', 'NYTimes.com' -> 'NYTimes')."""
    for line in body_lines[:max_lines]:
        m = SIGNIN_SITE_RE.search(line)
        if m:
            return domain_to_site_name(m.group(1))
    return None

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

    lines    = text.splitlines()
    nonempty = [l.strip() for l in lines if l.strip()]

    if not nonempty:
        return '', 'Untitled', 'Unknown Author', ''

    # Format A (most browsers): Site / Title / Author / ... / reading-time marker / body
    reading_time_idx = None
    for i, line in enumerate(nonempty[:10]):
        if READING_TIME_RE.match(line):
            reading_time_idx = i
            break

    # Format B (Firefox on iOS): Title / bare date-time line / body -- no
    # site name, no author, no reading-time marker.
    date_idx = None
    if reading_time_idx is None:
        for i, line in enumerate(nonempty[:10]):
            if DATE_LINE_RE.match(line):
                date_idx = i
                break

    # Format C (Polygon on iOS): Title / Author / UI chrome / "Published
    # <date>" -- no site name, no reading-time marker, date line has a
    # "Published" prefix and trailing timezone so it never matches Format B.
    published_idx = None
    if reading_time_idx is None and date_idx is None:
        for i, line in enumerate(nonempty[:15]):
            if PUBLISHED_LINE_RE.match(line):
                published_idx = i
                break

    # Format D (wire-service / bylined articles, no site line, no
    # reading-time marker, no date/Published line -- e.g. AP pieces synced
    # via Apple News or similar): Title / "BY <name(s)>" / body, or
    # occasionally the byline before the title. Without this, the fallback
    # below assumes a 3-line site/title/author header and shifts every
    # field by one -- the byline becomes the "title" (breaking every future
    # article sharing that byline, since the title never changes), and the
    # real first paragraph of body text gets swallowed as the "author".
    # Only checked at index 0/1 -- a byline at index 2 already matches the
    # ordinary site/title/author fallback correctly and is left alone.
    byline_idx = None
    if reading_time_idx is None and date_idx is None and published_idx is None:
        for i, line in enumerate(nonempty[:2]):
            if BYLINE_RE.match(line):
                byline_idx = i
                break

    if reading_time_idx is not None:
        header_lines = nonempty[:reading_time_idx]
        site   = header_lines[0] if len(header_lines) > 0 else ''
        title  = header_lines[1] if len(header_lines) > 1 else 'Untitled'
        author = header_lines[2] if len(header_lines) > 2 else 'Unknown Author'
        author = clean_author(author)
        body_source = nonempty[reading_time_idx + 1:]

    elif date_idx is not None:
        header_lines = nonempty[:date_idx]
        title  = header_lines[0] if header_lines else 'Untitled'
        site   = ''
        author = 'Unknown Author'
        body_source = nonempty[date_idx + 1:]
        # Some variants still include a "By <name>" line right after the date
        if body_source and BYLINE_RE.match(body_source[0]):
            author      = clean_author(body_source[0])
            body_source = body_source[1:]

    elif published_idx is not None:
        title  = nonempty[0] if nonempty else 'Untitled'
        site   = ''
        author = 'Unknown Author'
        # Scan lines between title and the Published line for the first
        # one that isn't known UI chrome (Follow, Link copied to
        # clipboard, By, or a bare vote/comment count) -- that's the author.
        for line in nonempty[1:published_idx]:
            if HEADER_JUNK_LINE_RE.match(line):
                continue
            if re.match(r'^\d+$', line):
                continue
            author = clean_author(line)
            break
        body_source = nonempty[published_idx + 1:]

    elif byline_idx is not None:
        if byline_idx == 0:
            author = clean_author(nonempty[0])
            title  = nonempty[1] if len(nonempty) > 1 else 'Untitled'
        else:  # byline_idx == 1
            title  = nonempty[0]
            author = clean_author(nonempty[1])
        site        = ''
        body_source = nonempty[byline_idx + 1:]

    else:
        header_lines = nonempty[:3]
        site   = header_lines[0] if len(header_lines) > 0 else ''
        title  = header_lines[1] if len(header_lines) > 1 else 'Untitled'
        author = header_lines[2] if len(header_lines) > 2 else 'Unknown Author'
        author = clean_author(author)
        body_source = nonempty[3:]

    # Site fallback chain: if we still don't have a site/source, check the
    # body for self-identifying strings before giving up. Order matters --
    # a wire-service dateline is checked first since it's the more specific
    # signal; the sign-in prompt is a broader net across more sites.
    if not site:
        site = detect_wire_service(body_source) or detect_signin_site(body_source)

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
