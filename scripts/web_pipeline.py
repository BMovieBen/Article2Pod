# web_pipeline.py
# Article add and text paste processing for web UI

import os, json, re, base64, time
import subprocess
import threading
from utils import (
    safe_slug, clean_author, get_temp_folder, get_input_folder,
    apply_phonetic_replacements, is_clipboard_domain, is_youtube_url,
    fetch_and_resize_image, JUNK_PATTERNS, READING_TIME_RE, parse_reader_mode,
    sanitize_filename, get_domain_override
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
                    # Use the script's own message if there is one, otherwise fall back to generic
                    script_msg = out.strip().splitlines()[-1].strip() if out.strip() else ''
                    user_msg   = (script_msg if script_msg
                                  else 'This site is blocking automated scraping. Please use Text mode and paste from Reader Mode.')
                    with _fetch_lock:
                        _fetch_results[fetch_id] = {
                            'status':         'switch_to_text',
                            'error':          user_msg,
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

    body   = apply_phonetic_replacements(body)
    # Byline line is left blank (not "Written by") when author is blank,
    # so nothing gets narrated -- but the line POSITION is kept stable
    # either way (title is always line 0, byline-or-blank is always line
    # 1), so update_article_metadata can reliably rewrite just that slot
    # later regardless of whether an author was ever present.
    header  = f'{title}\r\n' + (f'Written by {author}' if author else '') + '\r\n\r\n\r\n'
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

    # Domain-based voice override — url covers URL mode; meta['album']
    # (the resolved site name) covers Text mode, where there's no URL to
    # key off of. get_domain_override() tries both.
    domain_override = get_domain_override(url, meta.get('album', ''))
    voice_override   = domain_override.get('voice_file') if domain_override else None

    art_b64 = None
    art     = meta.get('album_art')
    if art and os.path.isfile(art):
        with open(art, 'rb') as f:
            art_b64 = 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode('utf-8')

    if os.path.isfile(os.path.join(temp, f'youtube-handoff-{slug}.json')):
        pipeline_type = 'youtube'
    elif os.path.isfile(os.path.join(temp, f'audio-handoff-{slug}.json')):
        pipeline_type = 'audio'
    else:
        pipeline_type = 'comfyui'

    item = {
        'slug':          slug,
        'status':        'pending',
        'title':         meta.get('title', slug),
        'artist':        meta.get('artist', ''),
        'album':         meta.get('album', ''),
        'album_art':     meta.get('album_art'),
        'source_url':    url,
        'error':         None,
        'added_at':      time.time(),
        'pipeline_type':        pipeline_type,
        'voice':                voice_override,
        'art_pending_comfyui':  meta.get('art_pending_comfyui', False),
        'fetch_output':         (fetch_output + '\n' + meta_out).strip(),
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

def _load_sidecar(json_path, slug):
    """Read temp/{slug}.json, or start a minimal one if it doesn't exist
    yet (e.g. YouTube items whose add-time yt-dlp lookup failed) -- so a
    user edit always has somewhere to persist."""
    if os.path.isfile(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'slug': slug}

def _save_sidecar(json_path, meta):
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

def update_article_metadata(slug, title, artist, album):
    """Overwrite title/author/site for a pending article (any pipeline)
    after fetch but before generation -- used by the metadata-edit UI to
    fix scraper mis-mapping without re-fetching. Updates temp/{slug}.json,
    rewrites just the first two header lines of temp/{slug}.txt when there
    is one (text articles only -- youtube/audio items have no narration
    text), and updates the queue item so the UI reflects it immediately.
    Sets metadata_edited in the sidecar so fetch-youtube.py, which
    re-reads yt-dlp metadata at processing time, keeps these values
    instead of clobbering them. Returns (ok, error)."""
    temp      = get_temp_folder()
    json_path = os.path.join(temp, f'{slug}.json')
    txt_path  = os.path.join(temp, f'{slug}.txt')

    with queue_lock:
        queue = load_queue()
        item  = next((i for i in queue if i['slug'] == slug), None)
        if not item:
            return False, 'Article not found in queue.'
        if item['status'] != 'pending':
            return False, 'Can only edit metadata for pending articles.'

        meta = _load_sidecar(json_path, slug)
        meta['title']           = title
        meta['artist']          = artist
        meta['album']           = album
        meta['metadata_edited'] = True
        _save_sidecar(json_path, meta)

        if os.path.isfile(txt_path):
            with open(txt_path, 'r', encoding='utf-8', newline='') as f:
                content = f.read()
            lines = content.split('\r\n')
            if len(lines) >= 2:
                lines[0] = title
                # Blank (not "Written by") when author is cleared, so it
                # doesn't get narrated -- same convention as process_text_paste.
                lines[1] = f'Written by {artist}' if artist else ''
                content  = '\r\n'.join(lines)
                with open(txt_path, 'w', encoding='utf-8', newline='') as f:
                    f.write(content)

        item['title']  = title
        item['artist'] = artist
        item['album']  = album
        save_queue(queue)

    print(f'[Article2Pod] Metadata updated for {slug}: "{title}" by {artist} ({album})')
    return True, None

def write_mp3_art(mp3_path, jpg_bytes):
    """Replace the embedded cover art (APIC) in an already-tagged MP3,
    leaving every other tag alone."""
    from mutagen.id3 import ID3, APIC, ID3NoHeaderError
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall('APIC')
    tags.add(APIC(encoding=3, mime='image/jpeg', type=3,
                  desc='Cover', data=jpg_bytes))
    tags.save(mp3_path)

def _image_to_jpeg_bytes(image_bytes):
    """Crop/resize to the standard 500x500 used by every other art source
    in this pipeline and re-encode as JPEG. Raises on unreadable input."""
    from io import BytesIO
    from utils import crop_image_bytes_to_square
    img = crop_image_bytes_to_square(image_bytes)
    buf = BytesIO()
    img.save(buf, 'JPEG', quality=90)
    return buf.getvalue()

def set_custom_article_art(slug, image_bytes):
    """Overwrite album art for a queue item with a user-uploaded/pasted/
    dropped image. Works for any pipeline type, in two states:

    - pending: overwrites temp/{slug}.jpg, which tag-mp3.py embeds later.
      Also cancels any pending ComfyUI art generation and sets art_custom
      in the sidecar -- otherwise generate-art.py (text articles) or
      fetch-youtube.py's thumbnail download (YouTube) would silently
      overwrite what the user just chose during processing.
    - done: rewrites the cover art embedded in the finished MP3 in the
      output folder, and temp/{slug}.jpg so the card shows it too.

    Returns (ok, error, jpg_bytes)."""
    temp      = get_temp_folder()
    json_path = os.path.join(temp, f'{slug}.json')
    jpg_path  = os.path.join(temp, f'{slug}.jpg')

    with queue_lock:
        queue = load_queue()
        item  = next((i for i in queue if i['slug'] == slug), None)
        if not item:
            return False, 'Article not found in queue.', None
        if item['status'] not in ('pending', 'done'):
            return False, 'Can only set custom art for pending or finished articles.', None

        try:
            jpg_bytes = _image_to_jpeg_bytes(image_bytes)
        except Exception as e:
            return False, f'Could not read that image: {e}', None

        if item['status'] == 'done':
            mp3_path = find_mp3_for_slug(slug, item.get('title', ''))
            if not mp3_path:
                return False, 'Could not find the finished MP3 to update.', None
            try:
                write_mp3_art(mp3_path, jpg_bytes)
            except Exception as e:
                return False, f'Could not update MP3 art: {e}', None
            print(f'[Article2Pod] Art replaced in: {mp3_path}')

        os.makedirs(temp, exist_ok=True)
        with open(jpg_path, 'wb') as f:
            f.write(jpg_bytes)

        if item['status'] == 'pending':
            meta = _load_sidecar(json_path, slug)
            meta['album_art']           = jpg_path
            meta['art_pending_comfyui'] = False
            meta['art_custom']          = True
            _save_sidecar(json_path, meta)
            item['art_pending_comfyui'] = False

        item['album_art'] = jpg_path
        save_queue(queue)

    print(f'[Article2Pod] Custom art set for {slug}')
    return True, None, jpg_bytes

def set_library_mp3_art(mp3_path, image_bytes):
    """Replace the cover art embedded in a library MP3 (one no longer in
    the queue). Returns (ok, error, jpg_bytes)."""
    try:
        jpg_bytes = _image_to_jpeg_bytes(image_bytes)
    except Exception as e:
        return False, f'Could not read that image: {e}', None
    try:
        write_mp3_art(mp3_path, jpg_bytes)
    except Exception as e:
        return False, f'Could not update MP3 art: {e}', None
    print(f'[Article2Pod] Art replaced in: {mp3_path}')
    return True, None, jpg_bytes

def find_mp3_for_slug(slug, title=''):
    """Find the MP3 in the output folder by title match."""
    import glob
    from utils import get_output_dir, sanitize_filename
    output_dir = get_output_dir()
    matches    = glob.glob(os.path.join(output_dir, '**', '*.mp3'), recursive=True)

    if not matches:
        return None

    # Try matching by sanitized title in filename
    if title:
        safe_title = sanitize_filename(title).lower()
        for m in matches:
            if safe_title[:20] in os.path.basename(m).lower():
                return m

    # Fallback: match by slug words
    slug_words = slug.replace('-', ' ').split()[:4]
    for m in matches:
        basename = os.path.basename(m).lower()
        if all(w in basename for w in slug_words):
            return m

    return None