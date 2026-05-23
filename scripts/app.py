# app.py
# Article2Pod Web UI - Flask routes

import os, sys, json, base64, glob, logging, webbrowser, threading, time
from flask import Flask, request, jsonify, render_template, send_file

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR     = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from utils import (
    load_config, get_temp_folder, get_input_folder,
    is_clipboard_domain, is_youtube_url, sanitize_filename,
    get_podcasts_folder
)
from queue_manager import (
    queue_lock, load_queue, save_queue, get_queue_item,
    delete_temp_files, cleanup_on_startup
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
    return jsonify({'queue': result, 'processing': state['processing']})

@app.route('/api/add', methods=['POST'])
def api_add():
    data = request.json
    url  = data.get('url', '').strip()
    text = data.get('text', '').strip()
    mode = data.get('mode', 'url')

    if not url and not text:
        return jsonify({'error': 'No URL or text provided.'}), 400

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
    return jsonify({'ok': True})

@app.route('/api/download/<slug>')
def api_download(slug):
    mp3_path = find_mp3_for_slug(slug)
    if not mp3_path:
        return jsonify({'error': 'MP3 not found.'}), 404
    item  = get_queue_item(slug)
    title = item.get('title', slug) if item else slug
    return send_file(mp3_path, mimetype='audio/mpeg',
                     as_attachment=True,
                     download_name=f'{title}.mp3')

@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify(get_state())

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

    threading.Thread(
        target=lambda: (time.sleep(1.5), webbrowser.open(f'http://localhost:{port}')),
        daemon=True
    ).start()

    print(f'Article2Pod Web UI running at http://localhost:{port}')
    print('Press Ctrl+C to stop.')
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)