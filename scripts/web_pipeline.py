# web_pipeline.py
# Article add and text paste processing for web UI

import os, json, re, base64
import subprocess
from utils import (
    safe_slug, clean_author, get_temp_folder, get_input_folder,
    apply_phonetic_replacements, is_clipboard_domain, is_youtube_url,
    fetch_and_resize_image, JUNK_PATTERNS, READING_TIME_RE, parse_reader_mode,
    sanitize_filename, get_podcasts_folder
)
from queue_manager import queue_lock, load_queue, save_queue

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

def run_script(script_name, *args):
    cmd    = ['python', os.path.join(SCRIPTS_DIR, script_name)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    return result.returncode == 0, result.stdout + result.stderr, result.returncode

def _should_switch_to_text(ok, out, code):
    if code == 2:
        return True
    if ok:
        return False
    signals = [
        'site appears to be blocking',
        'connection failed, site may be blocking',
        'known unsupported site',
        'switching to clipboard mode',
        'press enter when clipboard',
    ]
    return any(s in out.lower() for s in signals)

def process_text_paste(text, temp, input_dir):
    """Process pasted reader mode text. Returns (ok, error_or_message, slug)."""
    site, title, author, body = parse_reader_mode(text)

    if not body:
        preview = '\n'.join(text.splitlines()[:10])
        return False, (
            f'Could not extract article text from pasted content. '
            f'Make sure you are copying from Reader Mode. '
            f'First 10 lines seen: {preview}'
        ), None

    slug = safe_slug(title)

    handoff = {'clipboard_author': author, 'clipboard_site': site,
               'clipboard_title': title, 'clipboard_slug': slug}
    os.makedirs(input_dir, exist_ok=True)
    with open(os.path.join(input_dir, 'clipboard-handoff.json'), 'w', encoding='utf-8') as f:
        json.dump(handoff, f)

    body = apply_phonetic_replacements(body)
    header  = f'{title}\r\nWritten by {author}\r\n\r\n\r\n'
    content = header + body.replace('\n', '\r\n') + '\r\n[pause:3000]'

    os.makedirs(temp, exist_ok=True)
    with open(os.path.join(temp, f'{slug}.txt'), 'w',
              encoding='utf-8', newline='\r\n') as f:
        f.write(content)

    return True, f'  Slug: {slug}', slug

def finish_add(slug, url, mode, fetch_output):
    """Run fetch-metadata and add to queue. Returns (response_dict, error, status_code)."""
    temp      = get_temp_folder()
    input_dir = get_input_folder()
    os.makedirs(temp,      exist_ok=True)
    os.makedirs(input_dir, exist_ok=True)

    with queue_lock:
        if any(i['slug'] == slug for i in load_queue()):
            return None, 'Article already in queue.', 400

    if mode == 'url' and url:
        ok, meta_out, _ = run_script('fetch-metadata.py', url)
    else:
        ok, meta_out, _ = run_script('fetch-metadata.py', '--clipboard')

    if not ok:
        return None, f'fetch-metadata failed:\n{meta_out}', 400

    json_path = os.path.join(temp, f'{slug}.json')
    if not os.path.isfile(json_path):
        return None, 'Metadata file not found after fetch.', 400

    with open(json_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)

    art_b64 = None
    art     = meta.get('album_art')
    if art and os.path.isfile(art):
        with open(art, 'rb') as f:
            art_b64 = 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode('utf-8')

    item = {
        'slug':       slug,
        'status':     'pending',
        'title':      meta.get('title', slug),
        'artist':     meta.get('artist', ''),
        'album':      meta.get('album', ''),
        'album_art':  meta.get('album_art'),
        'source_url': url,
        'error':      None,
    }

    with queue_lock:
        queue = load_queue()
        queue.append(item)
        save_queue(queue)

    return {
        'slug':          slug,
        'title':         item['title'],
        'artist':        item['artist'],
        'album':         item['album'],
        'album_art_b64': art_b64,
        'fetch_output':  fetch_output + '\n' + meta_out,
    }, None, 200

def find_mp3_for_slug(slug, title=''):
    """Find the MP3 in podcasts folder for a given slug."""
    import glob
    podcasts = get_podcasts_folder()
    # Try temp first (copy saved by tag-mp3.py)
    temp_mp3 = os.path.join(get_temp_folder(), f'{slug}.mp3')
    if os.path.isfile(temp_mp3):
        return temp_mp3
    # Search podcasts folder
    matches = glob.glob(os.path.join(podcasts, '**', '*.mp3'), recursive=True)
    for m in matches:
        if slug in os.path.basename(m).lower():
            return m
        if title and title[:20].lower() in os.path.basename(m).lower():
            return m
    return None