# generate-art.py
# Usage: python generate-art.py <slug>
# Reads the article title from temp/{slug}.json (same pattern as
# tag-mp3.py), generates album art via ComfyUI, and overwrites
# temp/{slug}.jpg on success. Failure here is always non-fatal to the
# caller -- whatever fallback art fetch-metadata already saved is left
# in place.

import os, sys, json, time
import requests
from utils import (
    get_comfy_url, get_art_workflow_file, get_art_prompt_node_title,
    get_art_save_node_class, get_art_generation_timeout,
    get_temp_folder, get_output_folder, log_generation
)
from art_prompt import build_art_prompt

COMFY_URL   = get_comfy_url()
TEMP_FOLDER = get_temp_folder()

def free_comfyui_memory():
    """Ask ComfyUI to unload models and free VRAM/RAM. Best-effort --
    never raises, since a failed free shouldn't block generation (worst
    case is a slower/more contended run, not a crash). Called both before
    generation (unloads VibeVoice so the image model has room) and after
    (unloads the image model so VibeVoice has room again for the next
    queue item)."""
    try:
        requests.post(f'{COMFY_URL}/free',
                      json={'unload_models': True, 'free_memory': True},
                      timeout=10)
        print('  Freed ComfyUI memory.')
    except Exception as e:
        print(f'  Could not free ComfyUI memory (continuing anyway): {e}')

def load_workflow():
    path = get_art_workflow_file()
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f'Art workflow file not found: {path}')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def find_prompt_node(workflow):
    """Locate the node that should receive the raw article prompt.

    If art_prompt_node_title is set, match ANY node in the workflow by
    _meta.title, regardless of class_type. This matters for workflows
    that do their own prompt expansion internally (e.g. feeding the raw
    prompt into a primitive text node upstream of an LLM/VLM node, which
    itself feeds the CLIPTextEncode) -- patching the CLIPTextEncode
    directly in that case would bypass the whole expansion chain.

    If no title is configured, fall back to auto-detecting a single
    CLIPTextEncode node, which is enough for simpler workflows with just
    one prompt node."""
    title = get_art_prompt_node_title()
    if title:
        for nid, n in workflow.items():
            if n.get('_meta', {}).get('title', '') == title:
                return nid
        raise ValueError(f'No node titled "{title}" found in art workflow.')

    candidates = [nid for nid, n in workflow.items()
                  if n.get('class_type') == 'CLIPTextEncode']
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(
            'No CLIPTextEncode node found in art workflow, and no '
            'art_prompt_node_title configured to locate the prompt input '
            'another way.')
    raise ValueError(
        f'Workflow has {len(candidates)} CLIPTextEncode nodes -- set '
        f'"art_prompt_node_title" in config.json to the title of the '
        f'node that should receive the article prompt.')

def patch_workflow(workflow, prompt_text, filename_prefix):
    node_id = find_prompt_node(workflow)
    inputs  = workflow[node_id].setdefault('inputs', {})
    if 'text' in inputs:
        inputs['text'] = prompt_text
    elif 'value' in inputs:
        inputs['value'] = prompt_text
    else:
        raise ValueError(
            f'Prompt node "{node_id}" has neither a "text" nor "value" '
            f'input to patch -- check art_prompt_node_title.')

    preview = prompt_text[:80] + ('...' if len(prompt_text) > 80 else '')
    print(f'  Prompt:   {preview}')

    save_class = get_art_save_node_class()
    for nid, n in workflow.items():
        if n.get('class_type') == save_class:
            n['inputs']['filename_prefix'] = filename_prefix
            print(f'  Output:   {filename_prefix}_*')
    return workflow

def submit_workflow(workflow):
    response = requests.post(f'{COMFY_URL}/prompt',
                             json={'prompt': workflow}, timeout=30)
    if response.status_code != 200:
        try:
            body = response.json()
        except Exception:
            body = response.text
        raise RuntimeError(
            f'ComfyUI rejected the workflow (HTTP {response.status_code}): {body}')
    return response.json().get('prompt_id')

def wait_for_image(prompt_id, timeout=300):
    """Poll /history/{prompt_id}. Returns (status, detail, output_path).
    Reads the actual output filename from history's 'outputs' block
    rather than glob+mtime -- an exact match instead of a best guess at
    which file just landed."""
    print('  Generating image...')
    elapsed, interval = 0, 3
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        try:
            r       = requests.get(f'{COMFY_URL}/history/{prompt_id}', timeout=10)
            history = r.json()
            if prompt_id not in history:
                continue
            entry  = history[prompt_id]
            status = entry.get('status', {})
            if status.get('status_str') == 'error':
                msgs   = status.get('messages', [])
                detail = '; '.join(str(m) for m in msgs) if msgs \
                         else 'ComfyUI reported an error.'
                return 'error', detail, None
            if status.get('completed'):
                for node_out in entry.get('outputs', {}).values():
                    for img in node_out.get('images', []):
                        filename  = img.get('filename')
                        subfolder = img.get('subfolder', '')
                        if not filename:
                            continue
                        path = os.path.join(get_output_folder(), subfolder, filename)
                        if os.path.isfile(path):
                            print(f'  Complete! ({elapsed}s)')
                            return 'success', '', path
                return 'error', 'Completed but no output image found in history.', None
        except Exception:
            pass
    print(f'  Timed out after {timeout}s')
    return 'timeout', f'Timed out after {timeout}s', None

def generate_art(slug, title):
    """Generate album art via ComfyUI for the given slug/title. Returns
    (ok, error). On success, overwrites temp/{slug}.jpg with a 500x500
    crop matching every other art source. Never raises -- caller treats
    any failure as non-fatal. Every attempt is logged to
    generation-log.csv (as '{slug}-art') alongside audio generation rows,
    so a silent failure or skip is always visible there."""
    start_time = time.time()
    free_comfyui_memory()
    try:
        workflow = load_workflow()
        prompt   = build_art_prompt(title)
        prefix   = f'art_{slug}'
        workflow = patch_workflow(workflow, prompt, prefix)

        prompt_id = submit_workflow(workflow)
        if not prompt_id:
            detail = 'Failed to get prompt_id from ComfyUI.'
            log_generation(f'{slug}-art', 0, '-', 'error',
                           time.time() - start_time, detail)
            return False, detail

        status, detail, output_path = wait_for_image(
            prompt_id, timeout=get_art_generation_timeout())
        if status != 'success':
            log_generation(f'{slug}-art', 0, '-', status,
                           time.time() - start_time, detail)
            return False, detail

        from PIL import Image
        img    = Image.open(output_path).convert('RGB')
        target = 500
        w, h   = img.size
        scale  = max(target / w, target / h)
        img    = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        left   = (img.width  - target) // 2
        top    = (img.height - target) // 2
        img    = img.crop((left, top, left + target, top + target))

        dest = os.path.join(TEMP_FOLDER, f'{slug}.jpg')
        img.save(dest, 'JPEG', quality=90)

        try:
            os.remove(output_path)
        except Exception:
            pass

        log_generation(f'{slug}-art', 0, '-', 'success',
                       time.time() - start_time, 'art generated')
        return True, ''
    except Exception as e:
        log_generation(f'{slug}-art', 0, '-', 'error',
                       time.time() - start_time, str(e))
        return False, str(e)
    finally:
        free_comfyui_memory()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python generate-art.py <slug>')
        sys.exit(1)

    slug      = sys.argv[1]
    json_path = os.path.join(TEMP_FOLDER, f'{slug}.json')
    if not os.path.isfile(json_path):
        print(f'  No metadata JSON found for slug: {slug}')
        sys.exit(1)

    with open(json_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)

    ok, err = generate_art(slug, meta.get('title', slug))
    if ok:
        print('  Art generation complete.')
    else:
        print(f'  Art generation failed: {err}')
        sys.exit(1)
