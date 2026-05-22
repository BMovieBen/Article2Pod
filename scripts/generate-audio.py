# generate-audio.py

import os, sys, json, time, glob, shutil
import requests
from utils import (
    load_config, get_comfy_url, get_workflow_file,
    get_input_folder, get_audio_folder, get_temp_folder,
    get_audio_output_prefix
)

COMFY_URL     = get_comfy_url()
WORKFLOW_FILE = get_workflow_file()
INPUT_FOLDER  = get_input_folder()
AUDIO_FOLDER  = get_audio_folder()
TEMP_FOLDER   = get_temp_folder()
OUTPUT_PREFIX = get_audio_output_prefix()

def get_voice_file():
    config     = load_config()
    voice_file = config.get('voice_file')
    if voice_file:
        full_path = os.path.join(INPUT_FOLDER, voice_file)
        if not os.path.isfile(full_path):
            print(f'Voice file from config not found: {full_path}')
            sys.exit(1)
        return voice_file
    voices = glob.glob(os.path.join(INPUT_FOLDER, '*.mp3'))
    if not voices:
        print(f'No voice clone MP3 found in {INPUT_FOLDER}')
        sys.exit(1)
    print(f'  No voice_file in config, defaulting to: {os.path.basename(voices[0])}')
    return os.path.basename(voices[0])

def load_workflow():
    with open(WORKFLOW_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_article_txt_from_workflow():
    for node_id, node in load_workflow().items():
        if node.get('class_type') == 'LoadTextFromFileNode':
            return os.path.basename(node.get('inputs', {}).get('file', 'input/article.txt'))
    return 'article.txt'

ARTICLE_TXT = get_article_txt_from_workflow()

def patch_workflow(workflow, voice_file):
    for node_id, node in workflow.items():
        ct = node.get('class_type', '')
        if ct == 'LoadAudio':
            node['inputs']['audio']   = voice_file
            node['inputs']['audioUI'] = f'/api/view?filename={voice_file}&type=input&subfolder='
            print(f'  Voice:    {voice_file}')
        elif ct == 'LoadTextFromFileNode':
            node['inputs']['file'] = f'input/{ARTICLE_TXT}'
            print(f'  Text:     input/{ARTICLE_TXT}')
        elif ct == 'SaveAudioMP3':
            node['inputs']['filename_prefix'] = OUTPUT_PREFIX
            print(f'  Output:   {OUTPUT_PREFIX}_*.mp3')
    return workflow

def submit_workflow(workflow):
    response = requests.post(f'{COMFY_URL}/prompt',
                             json={'prompt': workflow}, timeout=30)
    response.raise_for_status()
    return response.json().get('prompt_id')

def wait_for_completion(prompt_id, timeout=3600):
    print(f'  Generating audio...')
    elapsed  = 0
    interval = 5
    spinner  = ['-', '\\', '|', '/']
    spin_idx = 0
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        try:
            r       = requests.get(f'{COMFY_URL}/history/{prompt_id}', timeout=10)
            history = r.json()
            if prompt_id in history:
                status = history[prompt_id].get('status', {})
                if status.get('completed'):
                    print(f'\r  Complete! ({elapsed}s)              ')
                    return True
                if status.get('status_str') == 'error':
                    print(f'\r  Error reported by ComfyUI.')
                    return False
        except Exception:
            pass
        spin = spinner[spin_idx % len(spinner)]
        spin_idx += 1
        print(f'\r  {spin} Generating... ({elapsed}s)', end='', flush=True)
    print(f'\r  Timed out after {timeout}s')
    return False

def rename_output(slug):
    pattern = os.path.join(AUDIO_FOLDER, 'podcast_*.mp3')
    files   = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        print(f'No output MP3 found matching: {pattern}')
        sys.exit(1)
    dest = os.path.join(AUDIO_FOLDER, f'{slug}.mp3')
    os.replace(files[0], dest)
    print(f'  Renamed:  {os.path.basename(files[0])} -> {slug}.mp3')
    return dest

def main(slug):
    voice_file = get_voice_file()
    print(f'  Slug:     {slug}')
    workflow  = load_workflow()
    workflow  = patch_workflow(workflow, voice_file)
    prompt_id = submit_workflow(workflow)
    if not prompt_id:
        print('Failed to get prompt_id from ComfyUI.')
        sys.exit(1)
    if not wait_for_completion(prompt_id):
        sys.exit(1)
    rename_output(slug)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python generate-audio.py <slug>')
        sys.exit(1)
    main(sys.argv[1])