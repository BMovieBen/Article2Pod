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

def get_all_voices():
    """Return list of (name, full_path) for all available voice MP3s."""
    from utils import get_voice_folder
    voice_folder = get_voice_folder()
    input_folder = get_input_folder()
    seen  = set()
    found = []
    for folder in [voice_folder, input_folder]:
        for path in glob.glob(os.path.join(folder, '*.mp3')):
            name = os.path.basename(path)
            if name not in seen:
                seen.add(name)
                found.append((name, path))
    return found

def get_voice_file(override=None):
    import random
    from utils import get_voice_folder
    voice_folder = get_voice_folder()
    input_folder = get_input_folder()

    # Shuffle override: pick a random voice
    if override == 'shuffle':
        voices = get_all_voices()
        if not voices:
            print(f'  No voice MP3s found for shuffle.')
            sys.exit(1)
        name, path = random.choice(voices)
        print(f'  Voice (shuffle): {name}')
        return name, path

    # Use override if provided
    if override:
        # Check voice folder first, then input folder
        for folder in [voice_folder, input_folder]:
            full_path = os.path.join(folder, override)
            if os.path.isfile(full_path):
                return override, full_path
        print(f'  Voice override not found: {override}, falling back to default.')

    config     = load_config()
    voice_file = config.get('voice_file')

    # Shuffle default: pick a random voice
    if voice_file == 'shuffle':
        voices = get_all_voices()
        if not voices:
            print(f'  No voice MP3s found for shuffle.')
            sys.exit(1)
        name, path = random.choice(voices)
        print(f'  Voice (shuffle): {name}')
        return name, path

    if voice_file:
        for folder in [voice_folder, input_folder]:
            full_path = os.path.join(folder, voice_file)
            if os.path.isfile(full_path):
                return voice_file, full_path

    # Fallback: first mp3 in voice folder, then input folder
    for folder in [voice_folder, input_folder]:
        voices = glob.glob(os.path.join(folder, '*.mp3'))
        if voices:
            name = os.path.basename(voices[0])
            print(f'  No voice_file in config, defaulting to: {name}')
            return name, voices[0]

    print(f'No voice clone MP3 found in {voice_folder} or {input_folder}')
    sys.exit(1)

def load_workflow():
    with open(WORKFLOW_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_article_txt_from_workflow():
    for node_id, node in load_workflow().items():
        if node.get('class_type') == 'LoadTextFromFileNode':
            return os.path.basename(node.get('inputs', {}).get('file', 'input/article.txt'))
    return 'article.txt'

ARTICLE_TXT = get_article_txt_from_workflow()

def patch_workflow(workflow, voice_file, voice_full_path):
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

    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        try:
            r       = requests.get(f'{COMFY_URL}/history/{prompt_id}', timeout=10)
            history = r.json()
            if prompt_id in history:
                status = history[prompt_id].get('status', {})
                if status.get('completed'):
                    print(f'  Complete! ({elapsed}s)')
                    return True
                if status.get('status_str') == 'error':
                    print(f'  Error reported by ComfyUI.')
                    return False
        except Exception:
            pass

    print(f'  Timed out after {timeout}s')
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

def main(slug, voice_override=None):
    voice_file, voice_full_path = get_voice_file(voice_override)
    print(f'  Slug:     {slug}')
    print(f'  Voice:    {voice_file}')

    # Copy voice file to ComfyUI input folder if not already there
    input_dest = os.path.join(INPUT_FOLDER, voice_file)
    if not os.path.isfile(input_dest):
        import shutil
        shutil.copy2(voice_full_path, input_dest)
        print(f'  Copied voice to input folder.')

    workflow  = load_workflow()
    workflow  = patch_workflow(workflow, voice_file, voice_full_path)
    prompt_id = submit_workflow(workflow)
    if not prompt_id:
        print('Failed to get prompt_id from ComfyUI.')
        sys.exit(1)
    if not wait_for_completion(prompt_id):
        sys.exit(1)
    rename_output(slug)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('slug')
    parser.add_argument('--voice', default=None)
    args = parser.parse_args()
    main(args.slug, args.voice)