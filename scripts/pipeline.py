# pipeline.py
# ComfyUI management and queue processing

import os, sys, json, time, shutil, subprocess, threading
import requests
from utils import (
    load_config, get_comfy_url, get_temp_folder,
    get_input_folder, get_audio_folder
)
from queue_manager import queue_lock, load_queue, save_queue, delete_temp_files

processing     = False
stop_requested = False
comfy_process  = None
current_slug   = None

def is_comfyui_running():
    try:
        r = requests.get(f'{get_comfy_url()}/system_stats', timeout=2)
        return r.status_code == 200
    except Exception:
        return False

def start_comfyui():
    global comfy_process
    config        = load_config()
    comfy_url     = get_comfy_url()
    comfy_base    = config['comfy_base']
    local_appdata = os.environ.get('LOCALAPPDATA', '')
    appdata       = os.environ.get('APPDATA', '')
    electron_rel  = config['comfy_electron_relative']
    main_py       = os.path.join(local_appdata, electron_rel, 'main.py')
    front_end     = os.path.join(local_appdata, electron_rel,
                                 'web_custom_versions', 'desktop_app')
    extra_models  = os.path.join(appdata, 'ComfyUI', 'extra_models_config.yaml')
    port          = comfy_url.split(':')[-1]

    args = [
        config['comfy_venv_python'], main_py,
        '--user-directory',           os.path.join(comfy_base, 'user'),
        '--input-directory',          config['input_folder'],
        '--output-directory',         config['output_folder'],
        '--front-end-root',           front_end,
        '--base-directory',           comfy_base,
        '--database-url',
            f"sqlite:///{comfy_base.replace(chr(92), '/')}/user/comfyui.db",
        '--extra-model-paths-config', extra_models,
        '--log-stdout', '--listen', '127.0.0.1',
        '--port', port, '--enable-manager', '--preview-method', 'auto',
    ]
    comfy_process = subprocess.Popen(args, cwd=comfy_base,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
    timeout = config.get('comfy_startup_timeout', 120)
    elapsed = 0
    while elapsed < timeout:
        time.sleep(2)
        elapsed += 2
        if is_comfyui_running():
            return True
    return False

def stop_comfyui():
    global comfy_process
    try:
        requests.post(f'{get_comfy_url()}/manager/reboot', timeout=3)
        time.sleep(2)
    except Exception:
        pass
    if comfy_process and comfy_process.poll() is None:
        comfy_process.terminate()
        try:
            comfy_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            comfy_process.kill()
    comfy_process = None

def run_script(script_name, *args):
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    cmd         = ['python', os.path.join(scripts_dir, script_name)] + list(args)
    result      = subprocess.run(cmd, capture_output=True, text=True,
                                 encoding='utf-8', errors='replace')
    return result.returncode == 0, result.stdout + result.stderr, result.returncode

def process_single(slug):
    temp      = get_temp_folder()
    input_dir = get_input_folder()
    audio_handoff   = os.path.join(temp, f'audio-handoff-{slug}.json')
    youtube_handoff = os.path.join(temp, f'youtube-handoff-{slug}.json')

    if os.path.isfile(youtube_handoff):
        with open(youtube_handoff) as f:
            hdata = json.load(f)
        ok, out, _ = run_script('fetch-youtube.py', hdata['source_url'], slug)
        if not ok:
            return False, f'fetch-youtube failed:\n{out}'
        os.remove(youtube_handoff)

    elif os.path.isfile(audio_handoff):
        with open(audio_handoff) as f:
            hdata = json.load(f)
        ok, out, _ = run_script('fetch-audio.py', hdata['source_url'], slug)
        if not ok:
            return False, f'fetch-audio failed:\n{out}'
        os.remove(audio_handoff)

    else:
        slug_txt    = os.path.join(temp, f'{slug}.txt')
        article_txt = os.path.join(input_dir, 'article.txt')
        if not os.path.isfile(slug_txt):
            return False, f'Article text file not found for slug: {slug}'
        shutil.copy2(slug_txt, article_txt)
        ok, out, _ = run_script('generate-audio.py', slug)
        if not ok:
            return False, f'generate-audio failed:\n{out}'

    ok, out, _ = run_script('tag-mp3.py', slug)
    if not ok:
        return False, f'tag-mp3 failed:\n{out}'

    return True, None

def process_queue():
    global processing, stop_requested, current_slug

    temp  = get_temp_folder()
    queue = load_queue()

    needs_comfyui = any(
        not os.path.isfile(os.path.join(temp, f'audio-handoff-{i["slug"]}.json')) and
        not os.path.isfile(os.path.join(temp, f'youtube-handoff-{i["slug"]}.json'))
        for i in queue if i['status'] == 'pending'
    )

    comfyui_started = False
    if needs_comfyui:
        if is_comfyui_running():
            with queue_lock:
                q = load_queue()
                for item in q:
                    if item['status'] == 'pending':
                        item['status'] = 'failed'
                        item['error']  = \
                            'ComfyUI is already running. Please close it first.'
                save_queue(q)
            processing = False
            return

        # Mark first pending item as processing immediately so UI shows spinner
        with queue_lock:
            q       = load_queue()
            pending = [i for i in q if i['status'] == 'pending']
            if pending:
                pending[0]['status'] = 'processing'
                current_slug         = pending[0]['slug']
                save_queue(q)

        print('[Article2Pod] Starting ComfyUI...')
        if not start_comfyui():
            with queue_lock:
                q = load_queue()
                for item in q:
                    if item['status'] in ('pending', 'processing'):
                        item['status'] = 'failed'
                        item['error']  = 'ComfyUI failed to start.'
                save_queue(q)
            processing   = False
            current_slug = None
            return

        print('[Article2Pod] ComfyUI ready.')
        comfyui_started = True

        # Reset the item we pre-marked back to pending so the loop picks it up normally
        with queue_lock:
            q = load_queue()
            for item in q:
                if item['slug'] == current_slug and item['status'] == 'processing':
                    item['status'] = 'pending'
            save_queue(q)
        current_slug = None

    try:
        while True:
            if stop_requested:
                print('[Article2Pod] Stop requested — halting queue.')
                break

            with queue_lock:
                q       = load_queue()
                pending = [i for i in q if i['status'] == 'pending']
                if not pending:
                    break
                item           = pending[0]
                item['status'] = 'processing'
                save_queue(q)

            slug         = item['slug']
            current_slug = slug
            print(f'[Article2Pod] Processing: {slug}')

            success, error = process_single(slug)
            current_slug   = None

            with queue_lock:
                q = load_queue()
                for qi in q:
                    if qi['slug'] == slug:
                        qi['status'] = 'done' if success else 'failed'
                        qi['error']  = None if success else error
                        break
                save_queue(q)

            if success:
                print(f'[Article2Pod] Done: {slug}')
            else:
                print(f'[Article2Pod] Failed: {slug}')
                print(f'  Reason: {error}')

            if stop_requested:
                print('[Article2Pod] Stop requested — halting queue.')
                break

    finally:
        if comfyui_started:
            stop_comfyui()
            print('[Article2Pod] ComfyUI shut down.')
        processing     = False
        stop_requested = False
        current_slug   = None
        pending_remain = [i for i in load_queue() if i['status'] == 'pending']
        if pending_remain:
            print(f'[Article2Pod] {len(pending_remain)} article(s) remaining in queue.')
        else:
            print('[Article2Pod] Queue complete.')

def start_processing():
    global processing, stop_requested
    processing     = True
    stop_requested = False
    threading.Thread(target=process_queue, daemon=True).start()

def request_stop():
    global stop_requested
    stop_requested = True

def get_state():
    return {
        'processing':     processing,
        'stop_requested': stop_requested,
        'current_slug':   current_slug,
    }