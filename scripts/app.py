# app.py
# Article2Pod Web UI - Flask routes

import os, sys, json, base64, glob, logging
from flask import Flask, request, jsonify, render_template, send_file

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR     = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from utils import (
    load_config, get_temp_folder, get_input_folder,
    is_clipboard_domain, is_youtube_url, sanitize_filename
)
from queue_manager import (
    queue_lock, load_queue, save_queue, get_queue_item,
    delete_temp_files, cleanup_on_startup, cleanup_orphaned_audio
)
from pipeline import (
    is_comfyui_running, get_state,
    start_processing, request_stop
)
from web_pipeline import (
    run_script, _should_switch_to_text,
    process_text_paste, finish_add, find_mp3_for_slug,
    start_fetch, get_fetch_result
)

app = Flask(__name__, template_folder=os.path.join(SCRIPTS_DIR, 'templates'))

class SuppressPollingFilter(logging.Filter):
    def filter(self, record):
        import re
        msg = record.getMessage()
        if re.search(r'"(GET|POST|PUT|DELETE|PATCH) .* HTTP/\d\.\d" [23]\d\d', msg):
            return False
        if '/api/add/poll/' in msg:
            return False
        if 'Bad request version' in msg or '\\x16\\x03' in msg:
            return False
        return True

logging.getLogger('werkzeug').addFilter(SuppressPollingFilter())

# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/queue', methods=['GET'])
def api_queue():
    queue  = load_queue()
    state  = get_state()
    queue  = sorted(queue, key=lambda i: i.get('added_at', 0), reverse=True)
    result = []
    for item in queue:
        entry = dict(item)
        art   = item.get('album_art')
        if art and os.path.isfile(art):
            with open(art, 'rb') as f:
                entry['album_art_b64'] = 'data:image/jpeg;base64,' + \
                    base64.b64encode(f.read()).decode('utf-8')
        else:
            entry['album_art_b64'] = None
        entry['is_current'] = (item['slug'] == state['current_slug'])
        result.append(entry)
    return jsonify({
        'queue':                 result,
        'processing':            state['processing'],
        'comfy_interrupt_error': state['comfy_interrupt_error'],
    })

@app.route('/api/add', methods=['POST'])
def api_add():
    data = request.json
    url  = data.get('url', '').strip()
    text = data.get('text', '').strip()
    mode = data.get('mode', 'url')

    if not url and not text:
        return jsonify({'error': 'No URL or text provided.'}), 400

    # URL mode — basic validation
    if mode == 'url' and url:
        if not url.startswith('http://') and not url.startswith('https://'):
            return jsonify({
                'error': 'Please enter a valid URL. To add plain text, use Text mode.',
            }), 400

    # Check blocked domains immediately without subprocess
    if mode == 'url' and is_clipboard_domain(url):
        return jsonify({
            'error':          'This site blocks scraping. Please use Text mode and paste from Reader Mode.',
            'switch_to_text': True,
        }), 400

    fetch_id = start_fetch(url, mode, text)
    return jsonify({'fetch_id': fetch_id})

@app.route('/api/add/poll/<fetch_id>', methods=['GET'])
def api_add_poll(fetch_id):
    result = get_fetch_result(fetch_id)
    if result is None:
        return jsonify({'error': 'Unknown fetch ID.'}), 404
    if result['status'] == 'pending':
        return jsonify({'status': 'pending'})
    if result['status'] == 'error':
        return jsonify({'status': 'error', 'error': result['error'],
                        'switch_to_text': result.get('switch_to_text', False)}), 400
    if result['status'] == 'switch_to_text':
        return jsonify({'status': 'error', 'error': result['error'],
                        'switch_to_text': True}), 400
    return jsonify({'status': 'done', **result['result']})

@app.route('/api/remove', methods=['POST'])
def api_remove():
    slug = request.json.get('slug')
    if not slug:
        return jsonify({'error': 'No slug provided.'}), 400
    with queue_lock:
        queue = load_queue()
        item  = next((i for i in queue if i['slug'] == slug), None)
        if not item:
            return jsonify({'error': 'Item not found.'}), 404
        if item['status'] == 'processing':
            return jsonify({'error': 'Cannot remove item currently being processed.'}), 400
        save_queue([i for i in queue if i['slug'] != slug])
        delete_temp_files(slug)
    print(f'[Article2Pod] Removed: {item.get("title", slug)}')
    return jsonify({'ok': True})

@app.route('/api/generate', methods=['POST'])
def api_generate():
    state = get_state()
    if state['processing']:
        return jsonify({'error': 'Already processing.'}), 400
    with queue_lock:
        q = load_queue()
        if not any(i['status'] in ('pending', 'failed') for i in q):
            return jsonify({'error': 'No pending articles.'}), 400
        for item in q:
            if item['status'] == 'failed':
                item['status'] = 'pending'
                item['error']  = None
        save_queue(q)
    start_processing()
    return jsonify({'ok': True})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    request_stop()
    state = get_state()
    return jsonify({
        'ok':                   True,
        'comfy_interrupt_error': state['comfy_interrupt_error'],
    })

@app.route('/api/retry', methods=['POST'])
def api_retry():
    """Reset a failed item back to pending so it gets reprocessed."""
    slug = request.json.get('slug')
    if not slug:
        return jsonify({'error': 'No slug provided.'}), 400
    with queue_lock:
        q    = load_queue()
        item = next((i for i in q if i['slug'] == slug), None)
        if not item:
            return jsonify({'error': 'Item not found.'}), 404
        if item['status'] != 'failed':
            return jsonify({'error': 'Item is not in failed state.'}), 400
        item['status'] = 'pending'
        item['error']  = None
        save_queue(q)
    print(f'[Article2Pod] Retrying: {item.get("title", slug)}')
    return jsonify({'ok': True})

@app.route('/api/download/<slug>')
def api_download(slug):
    item  = get_queue_item(slug)
    title = item.get('title', slug) if item else slug
    mp3_path = find_mp3_for_slug(slug, title)
    if not mp3_path:
        return jsonify({'error': 'MP3 not found.'}), 404
    return send_file(mp3_path, mimetype='audio/mpeg',
                     as_attachment=True,
                     download_name=f'{title}.mp3')

@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify(get_state())

@app.route('/api/delete', methods=['POST'])
def api_delete():
    slug = request.json.get('slug')
    if not slug:
        return jsonify({'error': 'No slug provided.'}), 400

    with queue_lock:
        queue = load_queue()
        item  = next((i for i in queue if i['slug'] == slug), None)
        if not item:
            return jsonify({'error': 'Item not found.'}), 404
        if item['status'] != 'done':
            return jsonify({'error': 'Can only delete completed items.'}), 400

        # Find and delete the MP3 from output folder
        from web_pipeline import find_mp3_for_slug
        from utils import get_output_dir
        mp3_path = find_mp3_for_slug(slug, item.get('title', ''))
        if mp3_path and os.path.isfile(mp3_path):
            os.remove(mp3_path)
            print(f'[Article2Pod] Deleted: {mp3_path}')
            # Clean up empty parent folders
            parent = os.path.dirname(mp3_path)
            output_dir = get_output_dir()
            for folder in [parent, os.path.dirname(parent)]:
                if folder != output_dir:
                    try:
                        if os.path.isdir(folder) and not os.listdir(folder):
                            os.rmdir(folder)
                    except Exception:
                        pass

        # Remove from queue and clean temp files
        save_queue([i for i in queue if i['slug'] != slug])
        delete_temp_files(slug)

    print(f'[Article2Pod] Removed from queue: {item.get("title", slug)}')
    return jsonify({'ok': True})

@app.route('/api/library', methods=['GET'])
def api_library():
    """Return all MP3s from output folder, excluding done queue items."""
    from utils import get_output_dir
    import glob

    # Get slugs of done queue items to exclude
    queue      = load_queue()
    done_slugs = {i['slug'] for i in queue if i['status'] == 'done'}

    # Scan output folder
    output_dir = get_output_dir()
    mp3s       = glob.glob(os.path.join(output_dir, '**', '*.mp3'), recursive=True)
    mp3s.sort(key=os.path.getmtime, reverse=True)

    items = []
    for mp3_path in mp3s:
        try:
            from mutagen.id3 import ID3
            tags   = ID3(mp3_path)
            title  = str(tags.get('TIT2', os.path.basename(mp3_path)))
            site   = str(tags.get('TPE1', ''))
            author = str(tags.get('TALB', ''))

            skip = False
            for slug in done_slugs:
                item = get_queue_item(slug)
                if item and item.get('title', '').lower() == title.lower():
                    skip = True
                    break
            if skip:
                continue

            art_b64 = None
            apic_tags = tags.getall('APIC')
            if apic_tags:
                art_b64 = 'data:image/jpeg;base64,' + \
                    base64.b64encode(apic_tags[0].data).decode('utf-8')

            rel_path = os.path.relpath(mp3_path, output_dir)
            items.append({
                'path':   rel_path,
                'title':  title,
                'site':   site,
                'author': author,
                'art_b64': art_b64,
            })
        except Exception as e:
            print(f'  [Library] Skipped {os.path.basename(mp3_path)}: {e}')
            continue

    return jsonify({'items': items, 'total': len(items)})

@app.route('/api/library/download', methods=['GET'])
def api_library_download():
    from utils import get_output_dir
    rel_path   = request.args.get('path')
    if not rel_path:
        return jsonify({'error': 'No path provided.'}), 400
    output_dir = get_output_dir()
    mp3_path   = os.path.normpath(os.path.join(output_dir, rel_path))
    if not mp3_path.startswith(os.path.normpath(output_dir)):
        return jsonify({'error': 'Invalid path.'}), 400
    if not os.path.isfile(mp3_path):
        return jsonify({'error': 'File not found.'}), 404
    filename = os.path.basename(mp3_path)
    return send_file(mp3_path, mimetype='audio/mpeg',
                     as_attachment=True,
                     download_name=filename)

@app.route('/api/library/delete', methods=['POST'])
def api_library_delete():
    """Delete an MP3 from the output folder by relative path."""
    from utils import get_output_dir
    rel_path   = request.json.get('path')
    if not rel_path:
        return jsonify({'error': 'No path provided.'}), 400

    output_dir = get_output_dir()
    mp3_path   = os.path.normpath(os.path.join(output_dir, rel_path))

    # Security check — ensure path stays within output_dir
    if not mp3_path.startswith(os.path.normpath(output_dir)):
        return jsonify({'error': 'Invalid path.'}), 400

    if not os.path.isfile(mp3_path):
        return jsonify({'error': 'File not found.'}), 404

    os.remove(mp3_path)
    print(f'[Article2Pod] Library delete: {rel_path}')

    # Clean up empty parent folders
    for folder in [os.path.dirname(mp3_path),
                   os.path.dirname(os.path.dirname(mp3_path))]:
        if folder != output_dir:
            try:
                if os.path.isdir(folder) and not os.listdir(folder):
                    os.rmdir(folder)
            except Exception:
                pass

    return jsonify({'ok': True})

@app.route('/api/voices', methods=['GET'])
def api_voices():
    """Return list of available voice sample MP3s."""
    import glob
    from utils import get_voice_folder, load_config
    voice_folder = get_voice_folder()
    default      = load_config().get('voice_file', '')

    names = sorted(
        os.path.basename(f)
        for f in glob.glob(os.path.join(voice_folder, '*.mp3'))
    )

    return jsonify({'voices': ['shuffle'] + names, 'default': default})

@app.route('/api/settings', methods=['POST'])
def api_settings():
    """Save default voice to config.json."""
    data  = request.json
    voice = data.get('voice_file', '').strip()
    if not voice:
        return jsonify({'error': 'No voice specified.'}), 400

    # Read, update, write config
    config_path = os.path.join(APP_DIR, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    config['voice_file'] = voice
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f'[Article2Pod] Default voice set to: {voice}')
    return jsonify({'ok': True})

@app.route('/api/queue/voice', methods=['PATCH'])
def api_queue_voice():
    """Set per-item voice override on a queue item."""
    data  = request.json
    slug  = data.get('slug', '').strip()
    voice = data.get('voice', '').strip()  # empty string = use default
    if not slug:
        return jsonify({'error': 'No slug specified.'}), 400

    with queue_lock:
        q    = load_queue()
        item = next((i for i in q if i['slug'] == slug), None)
        if not item:
            return jsonify({'error': 'Item not found.'}), 404
        item['voice'] = voice if voice else None
        save_queue(q)

    print(f'[Article2Pod] Voice for {slug} set to: {voice or "default"}')
    return jsonify({'ok': True})

# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    config    = load_config()
    port      = config.get('web_port', 8080)
    temp      = get_temp_folder()
    input_dir = get_input_folder()

    os.makedirs(temp,      exist_ok=True)
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(os.path.join(APP_DIR, 'log'), exist_ok=True)

    cleanup_on_startup(config.get('log_level', 'off'))
    cleanup_orphaned_audio(config.get('log_level', 'off'))

    print(f'Article2Pod Web UI running at http://localhost:{port}')
    print('Press Ctrl+C to stop.')
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
