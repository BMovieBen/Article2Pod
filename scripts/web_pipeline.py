# web_pipeline.py
# Article add and text paste processing for web UI

import os, json, re, base64, time
import subprocess
import threading
from utils import (
    safe_slug, clean_author, get_temp_folder, get_input_folder,
    apply_phonetic_replacements, is_clipboard_domain, is_youtube_url,
    fetch_and_resize_image, JUNK_PATTERNS, READING_TIME_RE, parse_reader_mode,
    sanitize_filename, get_podcasts_folder
)
from queue_manager import queue_lock, load_queue, save_queue

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_fetch_results = {}  # slug -> result dict
_fetch_lock    = threading.Lock()

def _run_fetch_background(fetch_id, url, mode, text=''):
    """Run fetch in background thread, store result for polling."""
    temp      = get_temp_folder()
    input_dir = get_input_folder()
    os.makedirs(temp,      exist_ok=True)
    os.makedirs(input_dir, exist_ok=True)

    try:
        if mode == 'text':
            ok, msg, slug = process_text_paste(text, temp, input_dir)
            if not ok:
                with _fetch_lock:
                    _fetch_results[fetch_id] = {'status': 'error', 'error': msg}
                return
        else:
            ok, out, code = run_script('fetch-article.py', url, '--web')
            if not ok or code == 2:
                if _should_switch_to_text(ok, out, code):
                    with _fetch_lock:
                        _fetch_results[fetch_id] = {
                            'status':         'switch_to_text',
                            'error':          'This site is blocking automated scraping. Please use Text mode and paste from Reader Mode.',
                            'switch_to_text': True,
                        }
                    return
                with _fetch_lock:
                    _fetch_results[fetch_id] = {'status': 'error', 'error': out}
                return

            slug = None
            for line in out.splitlines():
                if line.strip().startswith('Slug:'):
                    slug = line.split(':', 1)[1].strip()
                    break
            if not slug:
                with _fetch_lock:
                    _fetch_results[fetch_id] = {
                        'status': 'error',
                        'error':  'Could not determine slug from output.'
                    }
                return
            msg = out

        # Run fetch-metadata
        result, error, status = finish_add(slug, url, mode, msg)
        if error:
            with _fetch_lock:
                _fetch_results[fetch_id] = {'status': 'error', 'error': error}
            return

        with _fetch_lock:
            _fetch_results[fetch_id] = {'status': 'done', 'result': result}

    except Exception as e:
        with _fetch_lock:
            _fetch_results[fetch_id] = {'status': 'error', 'error': str(e)}

def start_fetch(url, mode, text=''):
    """Start a background fetch. Returns fetch_id for polling."""
    import uuid
    fetch_id = str(uuid.uuid4())[:8]
    with _fetch_lock:
        _fetch_results[fetch_id] = {'status': 'pending'}
    t = threading.Thread(
        target=_run_fetch_background,
        args=(fetch_id, url, mode, text),
        daemon=True
    )
    t.start()
    return fetch_id

def get_fetch_result(fetch_id):
    """Get result of a background fetch. Returns None if not found."""
    with _fetch_lock:
        result = _fetch_results.get(fetch_id)
        if result and result.get('status') in ('done', 'error', 'switch_to_text'):
            # Clean up after reading
            del _fetch_results[fetch_id]
        return result

def run_script(script_name, *args, timeout=120):
    cmd = ['python', os.path.join(SCRIPTS_DIR, script_name)] + list(args)
    print(f'[Article2Pod] Running: {script_name} {" ".join(str(a) for a in args if not a.startswith("--"))}')
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=timeout
        )
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                print(f'  {line}')
        if result.returncode != 0 and result.stderr.strip():
            for line in result.stderr.strip().splitlines():
                print(f'  [stderr] {line}')
        return result.returncode == 0, result.stdout + result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        print(f'  [ERROR] {script_name} timed out after {timeout}s')
        return False, f'{script_name} timed out after {timeout}s', 1

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

    body    = apply_phonetic_replacements(body)
    header  = f'{title}\r\nWritten by {author}\r\n\r\n\r\n'
    content = header + body.replace('\n', '\r\n') + '\r\n[pause:3000]'

    os.makedirs(temp, exist_ok=True)
    with open(os.path.join(temp, f'{slug}.txt'), 'w',
              encoding='utf-8', newline='\r\n') as f:
        f.write(content)

    print(f'[Article2Pod] Text paste processed: {slug}')
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
        print(f'[Article2Pod] fetch-metadata failed for: {slug}')
        return None, f'fetch-metadata failed:\n{meta_out}', 400

    json_path = os.path.join(temp, f'{slug}.json')
    if not os.path.isfile(json_path):
        print(f'[Article2Pod] Metadata JSON not found for slug: {slug}')
        print(f'  Expected: {json_path}')
        print(f'  Files in temp: {os.listdir(temp)}')
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
        'added_at':   time.time(),
    }

    with queue_lock:
        queue = load_queue()
        queue.append(item)
        save_queue(queue)

    print(f'[Article2Pod] Added to queue: {meta.get("title", slug)}')

    return {
        'slug':          slug,
        'title':         item['title'],
        'artist':        item['artist'],
        'album':         item['album'],
        'album_art_b64': art_b64,
        'fetch_output':  fetch_output + '\n' + meta_out,
    }, None, 200

def find_mp3_for_slug(slug, title=''):
    """Find the MP3 in temp or podcasts folder for a given slug."""
    import glob
    temp_mp3 = os.path.join(get_temp_folder(), f'{slug}.mp3')
    if os.path.isfile(temp_mp3):
        return temp_mp3
    podcasts = get_podcasts_folder()
    matches  = glob.glob(os.path.join(podcasts, '**', '*.mp3'), recursive=True)
    for m in matches:
        if slug in os.path.basename(m).lower():
            return m
        if title and title[:20].lower() in os.path.basename(m).lower():
            return m
    return None