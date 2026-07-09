# generate-audio.py

import os, sys, json, time, glob, shutil, datetime, csv, subprocess, re
import requests
from utils import (
    load_config, get_comfy_url, get_workflow_file,
    get_input_folder, get_audio_folder, get_temp_folder,
    get_audio_output_prefix, get_generation_logging_enabled,
    get_chunk_word_count, APP_DIR
)

COMFY_URL     = get_comfy_url()
WORKFLOW_FILE = get_workflow_file()
INPUT_FOLDER  = get_input_folder()
AUDIO_FOLDER  = get_audio_folder()
TEMP_FOLDER   = get_temp_folder()
OUTPUT_PREFIX = get_audio_output_prefix()

def get_voice_file(override=None):
    from utils import get_voice_folder
    voice_folder = get_voice_folder()

    # Use override if provided
    if override:
        full_path = os.path.join(voice_folder, override)
        if os.path.isfile(full_path):
            return override, full_path
        print(f'  Voice override not found: {override}, falling back to default.')

    config     = load_config()
    voice_file = config.get('voice_file')
    if voice_file:
        full_path = os.path.join(voice_folder, voice_file)
        if os.path.isfile(full_path):
            return voice_file, full_path

    # Fallback: first mp3 in voice folder
    voices = glob.glob(os.path.join(voice_folder, '*.mp3'))
    if voices:
        name = os.path.basename(voices[0])
        print(f'  No voice_file in config, defaulting to: {name}')
        return name, voices[0]

    print(f'No voice clone MP3 found in {voice_folder}')
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

def patch_workflow(workflow, voice_file, voice_full_path,
                   text_file=None, filename_prefix=None):
    text_file       = text_file or ARTICLE_TXT
    filename_prefix = filename_prefix or OUTPUT_PREFIX
    for node_id, node in workflow.items():
        ct = node.get('class_type', '')
        if ct == 'LoadAudio':
            node['inputs']['audio']   = voice_file
            node['inputs']['audioUI'] = f'/api/view?filename={voice_file}&type=input&subfolder='
            print(f'  Voice:    {voice_file}')
        elif ct == 'LoadTextFromFileNode':
            node['inputs']['file'] = f'input/{text_file}'
            print(f'  Text:     input/{text_file}')
        elif ct == 'SaveAudioMP3':
            node['inputs']['filename_prefix'] = filename_prefix
            print(f'  Output:   {filename_prefix}_*.mp3')
    return workflow

def count_words(path):
    """Word count of the text file actually sent to the TTS node."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return len(f.read().split())
    except Exception:
        return 0

PAUSE_MARKER = '\r\n[pause:3000]'

# Best-effort sentence boundary: a sentence-ending mark (optionally
# followed by a closing quote), then whitespace, then what looks like the
# start of a new sentence. Not a perfect NLP tokenizer (e.g. can misfire
# on abbreviations like "U.S." or "Mr."), but for chunk-boundary purposes
# an occasional early/late split is harmless -- it never cuts mid-word,
# and true mid-sentence cuts are rare in practice.
SENTENCE_SPLIT_RE = re.compile(
    r'(?<=[.!?])[\'"\u2019\u201d]?\s+(?=[A-Z0-9"\u2018\u201c(])'
)

def split_into_sentences(paragraph):
    """Best-effort sentence split of a single paragraph/line."""
    paragraph = paragraph.strip()
    if not paragraph:
        return []
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]

def split_text_into_chunks(text, target_words):
    """Split article text into chunks that always end on a complete
    sentence, each targeting ~target_words words.

    Splits at the sentence level rather than the line level -- this is
    deliberate: some sources (e.g. pasted Reader Mode text) don't preserve
    one-paragraph-per-line structure, and a line-boundary-only splitter
    can end up with the entire article as a single oversized "line" with
    nowhere to break. Sentence-level splitting always has somewhere to
    break as long as the text has more than one sentence.

    Paragraph structure (line breaks, blank lines -- including the
    header's spacing) is preserved: consecutive sentences from the same
    original line are rejoined with a space, sentences starting a new
    line are rejoined with \\r\\n. The trailing [pause:3000] marker is
    stripped and re-attached to the last chunk only.
    """
    pause_suffix = ''
    if text.endswith(PAUSE_MARKER):
        text         = text[:-len(PAUSE_MARKER)]
        pause_suffix = PAUSE_MARKER

    # Flatten into (sentence_text, starts_new_paragraph) atoms.
    atoms = []
    for para in text.split('\r\n'):
        if not para.strip():
            atoms.append(('', True))  # blank line -- preserves spacing
            continue
        sentences = split_into_sentences(para) or [para.strip()]
        for i, sent in enumerate(sentences):
            atoms.append((sent, i == 0))

    chunks        = []
    current_atoms = []
    current_words = 0

    for atom_text, is_new_para in atoms:
        atom_words = len(atom_text.split())
        if current_atoms and current_words + atom_words > target_words:
            chunks.append(current_atoms)
            current_atoms = []
            current_words = 0
        current_atoms.append((atom_text, is_new_para))
        current_words += atom_words

    if current_atoms:
        chunks.append(current_atoms)
    if not chunks:
        chunks = [[('', True)]]

    def render(chunk_atoms):
        parts = []
        for atom_text, is_new_para in chunk_atoms:
            sep = '\r\n' if is_new_para else ' '
            parts.append((sep + atom_text) if parts else atom_text)
        return ''.join(parts)

    rendered = [render(c) for c in chunks]
    rendered[-1] += pause_suffix
    return rendered

def get_generation_log_path():
    log_dir = os.path.join(APP_DIR, 'log')
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, 'generation-log.csv')

def log_generation(slug, word_count, voice_file, status, duration, detail=''):
    """Append one row per generation attempt: word count, duration, and
    outcome, so timeout thresholds can be worked out from real data.
    No-ops if generation_logging_enabled is false in config.json."""
    if not get_generation_logging_enabled():
        return
    log_path = get_generation_log_path()
    is_new   = not os.path.isfile(log_path)
    with open(log_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(['timestamp', 'slug', 'word_count', 'voice',
                             'status', 'duration_seconds', 'detail'])
        writer.writerow([
            datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            slug, word_count, voice_file, status,
            f'{duration:.1f}', detail,
        ])
    print(f'  Logged:   {status} in {duration:.1f}s ({word_count} words) -> {log_path}')

def submit_workflow(workflow):
    response = requests.post(f'{COMFY_URL}/prompt',
                             json={'prompt': workflow}, timeout=30)
    response.raise_for_status()
    return response.json().get('prompt_id')

def log_comfyui_error(prompt_id, history_entry):
    """Write ComfyUI error details to log/comfyui-errors.log."""
    log_dir  = os.path.join(APP_DIR, 'log')
    log_path = os.path.join(log_dir, 'comfyui-errors.log')
    os.makedirs(log_dir, exist_ok=True)

    lines = [
        f'\n=== {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===',
        f'Prompt ID: {prompt_id}',
    ]

    status = history_entry.get('status', {})
    msgs   = status.get('messages', [])
    if msgs:
        lines.append('Messages:')
        for msg in msgs:
            lines.append(f'  {msg}')

    try:
        lines.append(f'Full status:\n{json.dumps(status, indent=2)}')
    except Exception:
        pass

    with open(log_path, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f'  Error details logged to: {log_path}')

def wait_for_completion(prompt_id, timeout=3600):
    """Returns (status, detail) where status is 'success', 'error', or
    'timeout', and detail is an error message (empty string on success)."""
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
                    return 'success', ''
                if status.get('status_str') == 'error':
                    print(f'  Error reported by ComfyUI.')
                    log_comfyui_error(prompt_id, history[prompt_id])
                    msgs   = status.get('messages', [])
                    detail = '; '.join(str(m) for m in msgs) if msgs \
                             else 'ComfyUI reported an error.'
                    return 'error', detail
        except Exception:
            pass

    print(f'  Timed out after {timeout}s')
    return 'timeout', f'Timed out after {timeout}s'

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

def write_chunk_file(text, filename):
    path = os.path.join(INPUT_FOLDER, filename)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    return path

def cleanup_temp_file(path):
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except Exception:
            pass

def find_chunk_output(slug, chunk_index):
    prefix_base = os.path.basename(OUTPUT_PREFIX)  # e.g. 'podcast'
    pattern     = os.path.join(AUDIO_FOLDER,
                               f'{prefix_base}_{slug}_c{chunk_index:02d}_*.mp3')
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    return files[0] if files else None

def generate_chunk(slug, chunk_index, chunk_text, voice_file, voice_full_path):
    """Submit one chunk to ComfyUI and wait for it. Returns
    (status, detail, word_count, duration, output_path)."""
    chunk_filename = f'article-{slug}-c{chunk_index:02d}.txt'
    chunk_path     = write_chunk_file(chunk_text, chunk_filename)
    word_count     = len(chunk_text.split())
    print(f'  Chunk {chunk_index}: {word_count} words')

    workflow = load_workflow()
    workflow = patch_workflow(
        workflow, voice_file, voice_full_path,
        text_file=chunk_filename,
        filename_prefix=f'{OUTPUT_PREFIX}_{slug}_c{chunk_index:02d}',
    )

    start_time = time.time()
    prompt_id  = submit_workflow(workflow)
    if not prompt_id:
        cleanup_temp_file(chunk_path)
        return ('error', 'Failed to get prompt_id from ComfyUI.',
                word_count, time.time() - start_time, None)

    status, detail = wait_for_completion(prompt_id)
    duration = time.time() - start_time
    cleanup_temp_file(chunk_path)

    if status != 'success':
        return status, detail, word_count, duration, None

    output_path = find_chunk_output(slug, chunk_index)
    if not output_path:
        expected = f'{os.path.basename(OUTPUT_PREFIX)}_{slug}_c{chunk_index:02d}_*.mp3'
        return ('error',
                f'Chunk {chunk_index} completed but its output MP3 was not '
                f'found (expected {expected}).',
                word_count, duration, None)

    return 'success', '', word_count, duration, output_path

def merge_chunks(chunk_paths, slug):
    """Concatenate chunk MP3s into {slug}.mp3 via ffmpeg's concat demuxer
    (stream copy — no re-encoding, no quality loss). Deletes the chunk
    files and the temp concat list once done."""
    if not shutil.which('ffmpeg'):
        raise RuntimeError(
            'ffmpeg is required to merge chunked audio but was not found on PATH.')

    dest      = os.path.join(AUDIO_FOLDER, f'{slug}.mp3')
    list_path = os.path.join(TEMP_FOLDER, f'concat-{slug}.txt')
    with open(list_path, 'w', encoding='utf-8') as f:
        for p in chunk_paths:
            safe_path = p.replace('\\', '/').replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")

    result = subprocess.run(
        ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path,
         '-c', 'copy', dest],
        capture_output=True, text=True
    )

    cleanup_temp_file(list_path)
    for p in chunk_paths:
        cleanup_temp_file(p)

    if result.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError(f'ffmpeg merge failed: {result.stderr}')

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

    article_path = os.path.join(INPUT_FOLDER, ARTICLE_TXT)
    with open(article_path, 'r', encoding='utf-8') as f:
        full_text = f.read()
    word_count = len(full_text.split())
    print(f'  Words:    {word_count}')

    chunk_target = get_chunk_word_count()

    # --- Short article: single-shot path (unchanged behavior) ---
    if word_count <= chunk_target:
        workflow  = load_workflow()
        workflow  = patch_workflow(workflow, voice_file, voice_full_path)

        start_time = time.time()
        prompt_id  = submit_workflow(workflow)
        if not prompt_id:
            log_generation(slug, word_count, voice_file, 'error',
                           time.time() - start_time,
                           'Failed to get prompt_id from ComfyUI.')
            print('Failed to get prompt_id from ComfyUI.')
            sys.exit(1)

        status, detail = wait_for_completion(prompt_id)
        log_generation(slug, word_count, voice_file, status,
                       time.time() - start_time, detail)

        if status != 'success':
            sys.exit(1)
        rename_output(slug)
        return

    # --- Long article: chunk, generate each piece, merge ---
    chunks = split_text_into_chunks(full_text, chunk_target)
    print(f'  Splitting into {len(chunks)} chunk(s) of ~{chunk_target} words each.')

    chunk_outputs  = []
    overall_start  = time.time()
    for i, chunk_text in enumerate(chunks, start=1):
        status, detail, c_words, c_duration, output_path = generate_chunk(
            slug, i, chunk_text, voice_file, voice_full_path)

        log_generation(f'{slug}-c{i:02d}', c_words, voice_file, status,
                       c_duration, detail)

        if status != 'success':
            for p in chunk_outputs:
                cleanup_temp_file(p)
            print(f'  Chunk {i}/{len(chunks)} failed: {detail}')
            sys.exit(1)

        print(f'  Chunk {i}/{len(chunks)} complete ({c_duration:.0f}s).')
        chunk_outputs.append(output_path)

    print(f'  Merging {len(chunk_outputs)} chunk(s)...')
    try:
        merge_chunks(chunk_outputs, slug)
    except Exception as e:
        log_generation(slug, word_count, voice_file, 'error',
                       time.time() - overall_start, str(e))
        print(f'  Merge failed: {e}')
        sys.exit(1)

    print(f'  Merged:   {slug}.mp3')
    log_generation(slug, word_count, voice_file, 'success',
                   time.time() - overall_start, f'{len(chunks)} chunk(s)')

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('slug')
    parser.add_argument('--voice', default=None)
    args = parser.parse_args()
    main(args.slug, args.voice)
