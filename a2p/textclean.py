"""Text and metadata clean-up rules carried over from the original
Article2Pod (site-name, title and author detection, junk-line stripping,
pull quotes, the Reader Mode paste parser, image helpers). Only the
settings access changed: see _cfg()."""
import io, json, os, re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from . import settings

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

APP_DIR = settings.ROOT


def _cfg():
    """The old config key names, served from the new settings."""
    s = settings.load()
    return {
        'strip_lines': s['strip_lines'], 'strip_pull_quotes': s['strip_pull_quotes'],
        'phonetic_replacements': s['pronunciations'],
        'clipboard_domains': [d for d, e in s['sites'].items() if e.get('paste_only')],
        'domain_overrides': s['sites'],
    }

load_config = _cfg

def get_user_agent():
    return 'Mozilla/5.0'

def get_ad_strip_markers():
    return settings.load()['ad_markers']


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
        return ''
    text = re.sub(r'^see\s+all\s+by\s*', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'^by\b\s*', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'\s+(reported\s+from|reporting\s+from|in\s+[A-Z][a-z]+).+$', '', text).strip()
    return re.sub(r'  +', ' ', text)

# A real byline is never this long -- even an elaborate multi-author credit
# ("By Jane Smith, John Doe and Foo Bar") stays well under 100 characters,
# while a body paragraph is almost always several hundred. Used as a
# universal safety net so an unreliable candidate degrades to blank
# instead of ever assigning a full paragraph to the author field. Blank
# (not 'Unknown Author') is deliberate throughout this file -- that
# placeholder should only ever be introduced by tag-mp3.py's safe_meta()
# at generation time, so the UI and metadata JSON can tell "we don't know
# the author" apart from "the author is literally named Unknown Author".
MAX_AUTHOR_LINE_LEN = 100
# Real headlines run well past a byline's length but rarely past this;
# anything longer in the first two lines is body prose.
MAX_TITLE_LINE_LEN = 200

def _safe_author(line, fallback=''):
    if not line or len(line) > MAX_AUTHOR_LINE_LEN:
        return fallback
    return clean_author(line)

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
    return ''

def get_site_name(soup, base_url):
    og = soup.find('meta', property='og:site_name')
    if og and og.get('content', '').strip():
        name = og['content'].strip()
        if not name.lower().startswith(('http://', 'https://', 'www.')):
            return name
        base_url = name if '://' in name else 'https://' + name     # some sites put their address there (azfamily.com)
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

def strip_byline_echo(text, author, scan_lines=25):
    """Sites often repeat the author's name as stray lines near the top (a
    linked byline), sometimes followed by a bio fragment such as "is the
    Verge's weekend editor...". Drop those lines; only the top of the text is
    scanned so a name in the body is never touched."""
    norm = lambda s: s.replace('’', "'").strip().lower()
    names = {norm(n) for n in re.split(r',|\band\b|&', author or '') if n.strip()}
    names.discard('')
    if not names:
        return text
    bio = re.compile(r'^(is|was|has|covers|writes|reports|joined)\b')
    lines = text.splitlines(keepends=True)
    kept, i = [], 0
    while i < len(lines):
        if i < scan_lines and norm(lines[i]) in names:
            i += 1
            while i < len(lines) and i < scan_lines and norm(lines[i]) in names:
                i += 1
            j = i
            while j < len(lines) and j < scan_lines and not lines[j].strip():
                j += 1
            if j < len(lines) and bio.match(lines[j].strip()):
                i = j + 1
            continue
        kept.append(lines[i])
        i += 1
    return ''.join(kept)

_PQ_QUOTES = re.compile("[‘’“”\"']")

def _pq_norm(line):
    line = _PQ_QUOTES.sub('', line.lower()).replace('—', ' ').replace('-', ' ')
    return ' '.join(re.sub(r'[^a-z0-9 ]', '', line).split())

def drop_pull_quotes(text, min_words=5, max_words=60):
    """Remove pull quotes: a short standalone line whose wording also
    appears inside a longer line elsewhere (magazine sites repeat a
    sentence from the body as a styled callout, which would be read aloud
    twice). The copy inside the longer line is kept."""
    lines = text.splitlines(keepends=True)
    norms = [_pq_norm(l) for l in lines]
    drop  = set()
    for i, ni in enumerate(norms):
        n_words = len(ni.split())
        if n_words < min_words or n_words > max_words:
            continue
        if any(j != i and len(nj) > len(ni) + 10 and ni in nj
               for j, nj in enumerate(norms)):
            drop.add(i)
    return ''.join(l for i, l in enumerate(lines) if i not in drop)

def apply_strip_lines(text, author=None, max_len=120):
    """Drop whole lines that are page chrome (share buttons, photo credits, etc).
    Each strip_lines entry is either an exact line (case-insensitive) or
    're:<pattern>' (case-insensitive regex, searched within the line).
    Exact entries and regexes anchored with ^ match at any length; other regex
    entries only apply to lines up to max_len, so real paragraphs that merely mention a keyword are safe.
    If author is given, echoes of the byline near the top are dropped too."""
    text = strip_byline_echo(text, author)
    if load_config().get('strip_pull_quotes', True):
        text = drop_pull_quotes(text)
    entries = load_config().get('strip_lines', [])
    exact, patterns = set(), []
    for e in entries:
        if e.startswith('re:'):
            patterns.append(re.compile(e[3:], re.IGNORECASE))
        else:
            exact.add(e.strip().lower())
    if not exact and not patterns:
        return text
    kept = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and (stripped.lower() in exact or any(
                p.search(stripped) for p in patterns
                if len(stripped) <= max_len or p.pattern.startswith('^'))):
            continue
        kept.append(line)
    return ''.join(kept)

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

CHROME_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

def http_get(url, timeout=15, **kwargs):
    """requests.get with the configured user agent; if the site rejects it
    (401/403/406 -- e.g. KTAR 403s the bare 'Mozilla/5.0'), retry once with a
    full Chrome user agent."""
    import requests
    headers = dict(kwargs.pop('headers', {}) or {})
    headers.setdefault('User-Agent', get_user_agent())
    r = requests.get(url, headers=headers, timeout=timeout, **kwargs)
    if r.status_code in (401, 403, 406):
        headers['User-Agent'] = CHROME_UA
        r = requests.get(url, headers=headers, timeout=timeout, **kwargs)
    return r

def fetch_and_resize_image(img_url, size=(500, 500)):
    """Download and resize/crop image to square. Returns PIL Image or None."""
    import requests
    from PIL import Image
    from io import BytesIO
    try:
        r = http_get(img_url, timeout=10)
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

def crop_image_bytes_to_square(image_bytes, size=(500, 500)):
    """Decode arbitrary image bytes -- PNG, JPEG, GIF, BMP, WEBP, or
    anything Pillow can open -- and return a center-cropped, resized
    square RGB PIL Image, matching the same convention as
    fetch_and_resize_image above. Format is sniffed from the bytes
    themselves (Pillow's own header detection), not trusted from a
    filename or declared MIME type, so it doesn't matter which of those
    formats the caller actually sends. An animated GIF/WEBP yields its
    first frame, since Image.open doesn't iterate frames on its own.
    Unlike fetch_and_resize_image, this RAISES on invalid/undecodable
    data rather than returning None -- the caller here is a direct user
    upload, so a bad file should surface a clear error instead of
    silently doing nothing."""
    from PIL import Image
    from io import BytesIO
    img = Image.open(BytesIO(image_bytes))
    img = img.convert('RGB')
    target_w, target_h = size
    orig_w, orig_h      = img.size
    scale    = max(target_w / orig_w, target_h / orig_h)
    scaled_w = int(orig_w * scale)
    scaled_h = int(orig_h * scale)
    img      = img.resize((scaled_w, scaled_h), Image.LANCZOS)
    left = (scaled_w - target_w) // 2
    top  = (scaled_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))

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

# Known reader-mode UI chrome and metadata phrases that can end up as
# the "short" candidate line but are never a real byline/site name --
# checked as a substring so partial matches (e.g. embedded in a longer
# mashed-together line) are still caught.
AUTHOR_JUNK_SUBSTRINGS = (
    'share', 'save', 'follow', 'subscribe', 'sign in', 'sign up',
    'link copied', 'add as preferred', 'hours ago', 'minutes ago',
    'days ago', 'min read', 'advertisement', 'listen to this',
    'topline', 'key facts', 'big number', 'key background', 'tangent',
)

def _is_junk_candidate(line):
    """True if `line` looks like reader-mode UI chrome, a date/time
    stamp, or other non-name metadata rather than a genuine author or
    site name. Reuses the existing reading-time/date/Published/header-
    junk patterns (each originally built for the old per-format
    detectors) as a general "does this look like known metadata"
    check, plus a few structural signals:
      - a '|' separator (e.g. a combined "Sep 3rd 2026|LOS ANGELES|4
        min read" byline/date/read-time composite)
      - an 'Updated'/'Published' prefix
      - a lowercase-to-uppercase letter transition with no space
        between them, which reliably indicates several reader-mode UI
        elements got concatenated with no separator during copy/paste
        (e.g. "9 hours agoShareSaveAdd as preferred on
        GoogleTiffanie Turnbull", or a byline mashed directly against
        a following dateline/timestamp with no space at all)
    plus a small list of known non-name UI/section-header words."""
    if (READING_TIME_RE.match(line) or DATE_LINE_RE.match(line) or
            PUBLISHED_LINE_RE.match(line) or HEADER_JUNK_LINE_RE.match(line)):
        return True
    if '|' in line:
        return True
    if re.match(r'^(updated|published)\b', line, re.IGNORECASE):
        return True
    if re.search(r'[a-z][A-Z]', line):
        return True
    lower = line.lower()
    return any(j in lower for j in AUTHOR_JUNK_SUBSTRINGS)

def parse_reader_mode(text):
    """
    Parse reader mode pasted text. Returns (site, title, author, body).

    Reader Mode header formats vary wildly across browsers and sites, and
    chasing each new layout with its own detection branch is a losing
    game -- there's always another site with a different order, or one
    that omits the line the previous fix depended on. This instead uses
    one simple, general rule based on the first two non-empty lines:

    - Compare their lengths. A real headline is reliably longer than a
      real byline or site name, which are both short; body prose is
      reliably much longer than either. Whichever of the two is longer
      is the title; if the shorter one passes a sanity check (see
      _is_junk_candidate), it becomes both the author AND the site --
      title accuracy matters far more than site accuracy, and there's
      no reliable way to tell a bare site name apart from a bare byline
      from position alone, so this deliberately doesn't try.
    - If NEITHER of the first two lines is short enough to plausibly be
      a byline/site name, the article's body has already started with
      no header at all -- the less-long line is taken as the title, and
      the longer one is folded back into the body rather than lost.
    - Whenever the short candidate looks like reader-mode UI chrome, a
      date/time stamp, or several concatenated elements with no
      separating space (see _is_junk_candidate), author and site are
      both left blank rather than populated with a wrong or garbled
      value. tag-mp3.py already substitutes a clean "Unknown Author" /
      "Unknown Site" placeholder for blanks at generation time, and a
      blank author also means no "Written by ..." line gets narrated at
      all (see process_text_paste) -- both preferable to reading a
      mangled string aloud.
    """
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2013', '-').replace('\u2014', '-')

    lines    = text.splitlines()
    nonempty = [l.strip() for l in lines if l.strip()]

    if not nonempty:
        return '', 'Untitled', '', ''
    if len(nonempty) == 1:
        return '', nonempty[0], '', ''

    line1, line2 = nonempty[0], nonempty[1]

    if (len(line1) > MAX_TITLE_LINE_LEN) != (len(line2) > MAX_TITLE_LINE_LEN):
        # Exactly one line is paragraph-length -- the pasted text has a
        # title but no byline, so the paragraph is the start of the body.
        # (Without this, the short title looked like a byline/site name
        # and the paragraph, being longer, was wrongly chosen as title.)
        if len(line1) > MAX_TITLE_LINE_LEN:
            title, body_first_line = line2, line1
        else:
            title, body_first_line = line1, line2
        candidate   = None
        body_source = [body_first_line] + nonempty[2:]
    elif len(line1) <= MAX_AUTHOR_LINE_LEN or len(line2) <= MAX_AUTHOR_LINE_LEN:
        # At least one of the first two lines is short enough to
        # plausibly be a byline/site name -- the longer is the title,
        # the shorter is the author/site candidate.
        if len(line1) >= len(line2):
            title, candidate = line1, line2
        else:
            title, candidate = line2, line1
        body_source = nonempty[2:]
    else:
        # Neither is short -- the body has already started. The
        # less-long line is the title; the longer one is actually the
        # first line of body text, folded back in rather than lost.
        if len(line1) <= len(line2):
            title, body_first_line = line1, line2
        else:
            title, body_first_line = line2, line1
        candidate   = None
        body_source = [body_first_line] + nonempty[2:]

    author = ''
    if candidate and not _is_junk_candidate(candidate):
        author = _safe_author(candidate)
    site = author

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
