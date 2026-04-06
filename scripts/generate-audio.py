# generate-audio.py
# Submits the VibeVoice workflow to ComfyUI API and waits for completion.
# Input:  C:\ComfyUI\input\article.txt  (fixed filename)
# Output: C:\ComfyUI\output\audio\podcast_*.mp3  (renamed to <slug>.mp3)

import os, sys, json, time, glob, shutil
import requests

from utils import load_config, get_comfy_url, get_workflow_file, get_input_folder, get_audio_folder, get_temp_folder, get_audio_output_prefix

COMFY_URL     = get_comfy_url()
WORKFLOW_FILE = get_workflow_file()
INPUT_FOLDER  = get_input_folder()
AUDIO_FOLDER  = get_audio_folder()
TEMP_FOLDER   = get_temp_folder()
OUTPUT_PREFIX = get_audio_output_prefix()

def get_slug():
    jsons = glob.glob(os.path.join(TEMP_FOLDER, '*.json'))
    if not jsons:
        print('No metadata JSON found in temp folder.')
        sys.exit(1)
    with open(jsons[0], 'r', encoding='utf-8') as f:
        meta = json.load(f)
    return meta.get('slug', 'untitled')

from utils import load_config

def get_voice_file():
    config     = load_config()
    voice_file = config.get('voice_file')

    if voice_file:
        full_path = os.path.join(INPUT_FOLDER, voice_file)
        if not os.path.isfile(full_path):
            print(f'Voice file from config not found: {full_path}')
            sys.exit(1)
        return voice_file

    # Fallback: first mp3 found in input folder
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
    """Read the input filename from the workflow rather than config."""
    workflow = load_workflow()
    for node_id, node in workflow.items():
        if node.get('class_type') == 'LoadTextFromFileNode':
            file_path = node.get('inputs', {}).get('file', 'input/article.txt')
            # Strip the 'input/' prefix — we just want the filename
            return os.path.basename(file_path)
    return 'article.txt'

ARTICLE_TXT = get_article_txt_from_workflow()

def copy_txt_for_workflow(slug):
    """Copy <slug>.txt to article.txt so the workflow always reads the same filename."""
    src = os.path.join(INPUT_FOLDER, f'{slug}.txt')
    dst = os.path.join(INPUT_FOLDER, ARTICLE_TXT)
    if not os.path.isfile(src):
        print(f'Article txt not found: {src}')
        sys.exit(1)
    shutil.copy2(src, dst)
    print(f'  Copied {slug}.txt → article.txt')



def patch_workflow(workflow, voice_file):
    """Update node values by class type rather than hardcoded node IDs."""
    for node_id, node in workflow.items():
        class_type = node.get('class_type', '')

        if class_type == 'LoadAudio':
            node['inputs']['audio'] = voice_file
            node['inputs']['audioUI'] = f'/api/view?filename={voice_file}&type=input&subfolder='
            print(f'  Voice:    {voice_file}')

        elif class_type == 'LoadTextFromFileNode':
            node['inputs']['file'] = f'input/{ARTICLE_TXT}'
            print(f'  Text:     input/{ARTICLE_TXT}')

        elif class_type == 'SaveAudioMP3':
            node['inputs']['filename_prefix'] = OUTPUT_PREFIX
            print(f'  Output:   {OUTPUT_PREFIX}_*.mp3')

    return workflow

def submit_workflow(workflow):
    payload  = {'prompt': workflow}
    response = requests.post(f'{COMFY_URL}/prompt', json=payload, timeout=30)
    response.raise_for_status()
    return response.json().get('prompt_id')

def wait_for_completion(prompt_id, timeout=3600):
    """Poll /history until the prompt is complete."""
    print(f'  Generating audio...')
    elapsed  = 0
    interval = 5
    spinner  = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']
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
                    print(f'\r  Error reported by ComfyUI.          ')
                    return False
        except Exception:
            pass
        spin = spinner[spin_idx % len(spinner)]
        spin_idx += 1
        print(f'\r  {spin} Generating... ({elapsed}s)', end='', flush=True)

    print(f'\r  Timed out after {timeout}s')
    return False

def rename_output(slug):
    """Find the newest podcast_*.mp3 and rename it to <slug>.mp3."""
    pattern = os.path.join(AUDIO_FOLDER, 'podcast_*.mp3')
    files   = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        print(f'No output MP3 found matching: {pattern}')
        sys.exit(1)
    newest   = files[0]
    dest     = os.path.join(AUDIO_FOLDER, f'{slug}.mp3')
    os.replace(newest, dest)
    print(f'  Renamed:  {os.path.basename(newest)} → {slug}.mp3')
    return dest

def main(slug):
    voice_file = get_voice_file()
    print(f'  Slug:     {slug}')

    # article.txt already copied by ps1
    workflow  = load_workflow()
    workflow  = patch_workflow(workflow, voice_file)
    prompt_id = submit_workflow(workflow)

    if not prompt_id:
        print('Failed to get prompt_id from ComfyUI.')
        sys.exit(1)

    success = wait_for_completion(prompt_id)
    if not success:
        sys.exit(1)

    rename_output(slug)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python generate-audio.py <slug>')
        sys.exit(1)
    main(sys.argv[1])