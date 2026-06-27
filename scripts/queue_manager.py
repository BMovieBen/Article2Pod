# queue_manager.py
# Queue load/save/cleanup

import os, json, threading
from utils import get_temp_folder, get_queue_file

queue_lock = threading.Lock()

def load_queue():
    qf = get_queue_file()
    if not os.path.isfile(qf):
        return []
    with open(qf, 'r', encoding='utf-8') as f:
        data = json.load(f)
    result = []
    for item in data:
        if isinstance(item, str):
            result.append({'slug': item, 'status': 'pending', 'title': item,
                           'artist': '', 'album': '', 'album_art': None,
                           'source_url': '', 'error': None})
        else:
            result.append(item)
    return result

def save_queue(queue):
    with open(get_queue_file(), 'w', encoding='utf-8') as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)

def get_queue_item(slug):
    return next((i for i in load_queue() if i['slug'] == slug), None)

def delete_temp_files(slug):
    temp = get_temp_folder()
    for pattern in [f'{slug}.txt', f'{slug}.json', f'{slug}.jpg',
                    f'{slug}.mp3', f'audio-handoff-{slug}.json',
                    f'youtube-handoff-{slug}.json', f'slug-handoff-{slug}.json']:
        path = os.path.join(temp, pattern)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except Exception:
                pass

def cleanup_on_startup(log_level='off'):
    temp = get_temp_folder()
    with queue_lock:
        queue   = load_queue()
        cleaned = []
        for item in queue:
            if item['status'] == 'done':
                delete_temp_files(item['slug'])
            else:
                if item['status'] == 'processing':
                    item['status'] = 'pending'
                    item['error']  = None
                cleaned.append(item)
        save_queue(cleaned)

        valid_slugs = {i['slug'] for i in cleaned}
        if os.path.isdir(temp):
            for f in os.listdir(temp):
                base = f
                for ext in ['.txt', '.json', '.jpg', '.mp3']:
                    base = base.replace(ext, '')
                for prefix in ['audio-handoff-', 'youtube-handoff-', 'slug-handoff-']:
                    base = base.replace(prefix, '')
                if base not in valid_slugs:
                    try:
                        os.remove(os.path.join(temp, f))
                        if log_level == 'verbose':
                            print(f'[startup] Removed orphaned temp file: {f}')
                    except Exception:
                        pass