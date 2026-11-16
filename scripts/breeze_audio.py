# breeze_audio.py
#
# The Breeze TTS 2 audio path, used by generate-audio.py when the audio
# workflow contains Breeze nodes (see tts_engine.py). Per article:
#   1. read the article text and its metadata sidecar
#   2. text model: genre + which sentences are quotes (breeze_text.py)
#   3. voice sample + transcript (breeze_voice.py)
#   4. group sentences into segments (narration / quote runs, <= max words)
#   5. one ComfyUI job per segment, each with the voice direction and
#      guidance of its kind; optional Whisper check with seed retries
#   6. join the segments with ffmpeg (edge silence trimmed, gaps inserted,
#      a single MP3 encode) into <audio_folder>/<slug>.mp3
# Everything that happened is written to temp/<slug>.breeze.json and
# log/breeze-articles.csv (when ai_stats_logging is on).

import os, sys, json, time, copy, random, subprocess, datetime
import requests
from utils import (
    get_comfy_url, get_temp_folder, get_input_folder, get_audio_folder, get_output_folder,
    get_breeze_config, get_breeze_preset, get_breeze_voice_settings, get_domain_override, get_ai_quality_config,
    register_comfy_prompt, unregister_comfy_prompt, stop_flag_set, prompt_vanished,
    free_comfyui_memory, log_ai_stats, log_ai_detail,
)
from tts_engine import find_node
import breeze_text, breeze_voice

TINY_RUN_WORDS = 7     # a narration run shorter than this ("Garcia said.") joins the quote before it


# ------------------------------------------------------------
# Inputs
# ------------------------------------------------------------
def read_article(slug, fallback_path):
    """Prefer temp/<slug>.txt (the cleaned article for this slug) so this
    never depends on the shared input/article.txt."""
    path = os.path.join(get_temp_folder(), f'{slug}.txt')
    if not os.path.isfile(path):
        path = fallback_path
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()

def read_meta(slug):
    path = os.path.join(get_temp_folder(), f'{slug}.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            side = json.load(f)
    except Exception:
        side = {}
    return {'title': side.get('title', ''), 'author': side.get('artist', ''),
            'site': side.get('album', ''), 'source_url': side.get('source_url', '')}


# ------------------------------------------------------------
# Segments
# ------------------------------------------------------------
def build_segments(parsed, sentences, preset, max_words):
    """Returns a list of dicts: kind (title|narr|quote), text, words,
    quote_start, quote_end."""
    segs = []
    title = parsed['title'].rstrip()
    if title:
        if title[-1] not in '.!?':
            title += '.'
        if parsed['byline']:
            title += ' ' + parsed['byline'].rstrip('.') + '.'
        segs.append({'kind': 'title', 'text': title})

    # runs of consecutive sentences with the same kind
    runs = []
    for s in sentences:
        kind = 'quote' if s['quote'] else 'narr'
        if runs and runs[-1]['kind'] == kind:
            runs[-1]['sents'].append(s)
        else:
            runs.append({'kind': kind, 'sents': [s]})
    # fold tiny narration runs (attributions) into the quote before them
    folded = []
    for r in runs:
        words = sum(s['words'] for s in r['sents'])
        if folded and r['kind'] == 'narr' and words < TINY_RUN_WORDS \
                and r['sents'][0]['para'] == folded[-1]['sents'][-1]['para']:
            folded[-1]['sents'].extend(r['sents'])
        elif folded and folded[-1]['kind'] == r['kind']:
            folded[-1]['sents'].extend(r['sents'])
        else:
            folded.append(r)

    cue = (preset.get('quote_cue') or '').strip()
    for r in folded:
        pieces, cur, cur_words, last_para = [], [], 0, None
        for s in r['sents']:
            if cur and cur_words + s['words'] > max_words:
                pieces.append(cur)
                cur, cur_words = [], 0
            cur.append(s)
            cur_words += s['words']
        if cur:
            pieces.append(cur)
        for pi, piece in enumerate(pieces):
            parts, prev_para = [], None
            for s in piece:
                if prev_para is not None and s['para'] != prev_para:
                    parts.append('\n')
                elif parts:
                    parts.append(' ')
                parts.append(s['text'])
                prev_para = s['para']
            text = ''.join(parts)
            quote_start = r['kind'] == 'quote' and pi == 0
            if quote_start and cue:
                text = breeze_text.insert_cue(text, cue)
            segs.append({'kind': r['kind'], 'text': text,
                         'quote_start': quote_start,
                         'quote_end': r['kind'] == 'quote' and pi == len(pieces) - 1})
    for s in segs:
        s['words'] = len(s['text'].split())
    return segs


def gap_after(seg, nxt, preset, cfg):
    if nxt is None:
        return 0.0
    if seg['kind'] == 'title':
        return float(cfg['title_gap'])
    if seg['kind'] == 'quote' and seg.get('quote_end'):
        return float(preset.get('quote_end_pause') or cfg['switch_gap'])
    if seg['kind'] != nxt['kind']:
        return float(cfg['switch_gap'])
    return float(cfg['sentence_gap'])


# ------------------------------------------------------------
# ComfyUI
# ------------------------------------------------------------
def patch(workflow, seg, slug, idx, ref_audio, ref_text, preset, seed):
    wf = copy.deepcopy(workflow)
    _, load = find_node(wf, 'LoadAudio')
    if load:
        load['inputs']['audio'] = ref_audio
        load['inputs'].pop('audioUI', None)
    nid, gen = find_node(wf, 'BreezeTTS2VoiceDirection')
    if gen is None:
        nid, gen = find_node(wf, 'BreezeTTS2VoiceClone')
    if gen is None:
        raise RuntimeError('The Breeze workflow needs a BreezeTTS2VoiceDirection (or VoiceClone) node.')
    quote = seg['kind'] == 'quote'
    inp = gen['inputs']
    inp['text'] = seg['text']
    inp['reference_text'] = ref_text
    inp['seed'] = int(seed)
    inp['temperature'] = float(preset['temperature'])
    if gen['class_type'] == 'BreezeTTS2VoiceDirection':
        inp['instruction'] = preset['quote'] if quote else preset['narrator']
        inp['cfg_scale'] = float(preset['quote_guidance'] if quote else preset['narrator_guidance'])
    _, save = find_node(wf, 'SaveAudio')
    if save is None:
        raise RuntimeError('The Breeze workflow needs a SaveAudio node.')
    save['inputs']['filename_prefix'] = f'audio/breeze_{slug}_s{idx:03d}'
    return wf


def submit_and_wait(wf, timeout=1800):
    """Returns (status, detail, history_entry). status: success | error |
    cancelled | timeout."""
    base = get_comfy_url()
    if stop_flag_set():
        return 'cancelled', 'Stop requested.', None
    r = requests.post(f'{base}/prompt', json={'prompt': wf}, timeout=30)
    if r.status_code != 200:
        return 'error', f'ComfyUI rejected the workflow: {r.text[:800]}', None
    pid = r.json().get('prompt_id')
    if not pid:
        return 'error', 'ComfyUI returned no prompt_id.', None
    register_comfy_prompt(pid)
    try:
        start, missing, unreachable = time.time(), 0, 0
        while time.time() - start < timeout:
            time.sleep(2)
            try:
                hist = requests.get(f'{base}/history/{pid}', timeout=10).json()
                unreachable = 0
            except Exception:
                unreachable += 1
                if unreachable >= 60:
                    return 'error', 'ComfyUI stopped responding.', None
                continue
            if pid in hist:
                entry  = hist[pid]
                status = entry.get('status', {})
                if status.get('status_str') == 'error':
                    msgs = '; '.join(str(m) for m in status.get('messages', []))
                    return 'error', f'ComfyUI error: {msgs[:1500]}', entry
                if status.get('completed'):
                    return 'success', '', entry
            else:
                missing = missing + 1 if prompt_vanished(pid, base) else 0
                if missing >= 2:
                    return 'cancelled', 'Cancelled (prompt removed from ComfyUI queue).', None
        try:
            requests.post(f'{base}/queue', json={'delete': [pid]}, timeout=10)
            requests.post(f'{base}/interrupt', timeout=10)
        except Exception:
            pass
        return 'timeout', f'Timed out after {timeout}s', None
    finally:
        unregister_comfy_prompt(pid)


def output_path(entry):
    for out in (entry or {}).get('outputs', {}).values():
        for a in out.get('audio', []) or []:
            return os.path.join(get_output_folder(), a.get('subfolder', ''), a['filename'])
    return None


def exec_seconds(entry, fallback):
    """ComfyUI's own execution time (excludes time queued behind other jobs)."""
    ts = {m[0]: m[1].get('timestamp') for m in (entry or {}).get('status', {}).get('messages', [])
          if isinstance(m, (list, tuple)) and len(m) == 2 and isinstance(m[1], dict)}
    if ts.get('execution_start') and ts.get('execution_success'):
        return (ts['execution_success'] - ts['execution_start']) / 1000.0
    return fallback


# ------------------------------------------------------------
# Assembly
# ------------------------------------------------------------
TRIM = ('silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,areverse,'
        'silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,areverse')

def assemble(paths, gaps, end_pause, dest, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    listing, made = [], []
    def silence(sec):
        p = os.path.join(work_dir, f'sil_{int(round(sec * 1000))}.wav')
        if not os.path.isfile(p):
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                            'anullsrc=r=24000:cl=mono', '-t', f'{sec:.3f}', '-c:a', 'pcm_s16le', p],
                           check=True)
            made.append(p)
        return p
    for i, (p, gap) in enumerate(zip(paths, gaps)):
        w = os.path.join(work_dir, f'seg_{i:03d}.wav')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', p, '-af', TRIM,
                        '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', w], check=True)
        made.append(w)
        listing.append(w)
        if gap > 0:
            listing.append(silence(gap))
    if end_pause > 0:
        listing.append(silence(end_pause))
    list_file = os.path.join(work_dir, 'concat.txt')
    with open(list_file, 'w', encoding='utf-8') as f:
        for p in listing:
            f.write("file '" + p.replace('\\', '/').replace("'", "'\\''") + "'\n")
    made.append(list_file)
    r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list_file,
                        '-c:a', 'libmp3lame', '-b:a', '128k', dest], capture_output=True, text=True)
    for p in made:
        try:
            os.remove(p)
        except Exception:
            pass
    try:
        os.rmdir(work_dir)
    except Exception:
        pass
    if r.returncode != 0 or not os.path.isfile(dest):
        raise RuntimeError(f'ffmpeg could not join the segments: {r.stderr[-800:]}')


# ------------------------------------------------------------
# Main entry
# ------------------------------------------------------------
def run(slug, workflow, voice_file, voice_full_path, fallback_text_path):
    """Generates <audio_folder>/<slug>.mp3. Returns (status, detail, stats)."""
    cfg     = get_breeze_config()
    started = time.time()
    meta    = read_meta(slug)
    parsed  = breeze_text.parse_article(read_article(slug, fallback_text_path))
    if not meta['title']:
        meta['title'] = parsed['title']
    sentences = breeze_text.build_sentences(parsed)
    site      = get_domain_override(meta['source_url'], meta['site']) or {}

    # 1. genre + quotes (text model)
    info = breeze_text.analyze(meta, sentences, cfg, genre_pinned=site.get('genre'))
    if info['genre_method'] == 'llm' or info['quote_method'] == 'llm' or info['errors']:
        free_comfyui_memory()          # hand the text model's VRAM back before Breeze loads
    preset = get_breeze_preset(info['genre'], site, cfg, voice_file)
    if get_breeze_voice_settings(voice_file, cfg):
        print(f'  Breeze:   using voice settings for {voice_file}')
    print(f'  Breeze:   genre {preset["genre"]} ({info["genre_method"]})'
          + (f', tone {"/".join(info["tone"])}' if info['tone'] else '')
          + f', quotes by {info["quote_method"]}'
          + (f', text model {info["llm_seconds"]:.0f}s' if info['llm_seconds'] else ''))
    for e in info['errors']:
        print(f'  Breeze:   text model problem ({e[:160]}); using fallbacks.')
    if '{tone}' in preset['narrator']:
        preset['narrator'] = preset['narrator'].replace('{tone}', ', '.join(info['tone']) or 'natural')

    # 2. voice sample + transcript
    qc = get_ai_quality_config()
    try:
        ref_audio, ref_text, ref_secs = breeze_voice.prepare_reference(
            voice_file, voice_full_path, float(cfg['max_reference_seconds']))
    except Exception as e:
        return 'error', str(e), None
    max_words = breeze_voice.max_words_for_reference(ref_secs, cfg['max_segment_words'])

    # 3. segments
    segs = build_segments(parsed, sentences, preset, max_words)
    n_quotes = sum(1 for s in segs if s.get('quote_start'))
    print(f'  Breeze:   {len(segs)} segment(s), {n_quotes} quote(s), max {max_words} words, '
          f'voice sample {ref_secs:.0f}s')

    # 4. generate
    worker = None
    if qc['mode'] != 'off':
        from ai_quality import ASRWorker
        worker = ASRWorker(qc)
        if not worker.start():
            print(f'  Quality check disabled for this run: {worker.error}')
            worker = None
    retry        = worker is not None and qc['mode'] == 'retry'
    max_attempts = max(1, int(qc['max_attempts'])) if retry else 1
    min_match    = float(qc['min_match'])
    paths, gen_total, seg_log = [], 0.0, []
    try:
        for i, seg in enumerate(segs, 1):
            attempts = []
            for n in range(1, max_attempts + 1):
                seed = int(cfg['seed']) if n == 1 else random.randint(1, 2**31 - 1)
                wf   = patch(workflow, seg, slug, i, ref_audio, ref_text, preset, seed)
                t0   = time.time()
                status, detail, entry = submit_and_wait(wf)
                secs = exec_seconds(entry, time.time() - t0)
                gen_total += secs
                if status != 'success':
                    for a in attempts:
                        _rm(a['path'])
                    for p in paths:
                        _rm(p)
                    return status, f'segment {i}: {detail}', None
                path = output_path(entry)
                if not path or not os.path.isfile(path):
                    return 'error', f'segment {i} finished but its audio file was not found.', None
                match = None
                if worker:
                    res   = worker.score(path, seg['text'])
                    match = res.get('match')
                attempts.append({'seed': seed, 'path': path, 'match': match, 'seconds': round(secs, 1)})
                # Short segments always get two word errors of slack (a 10-word
                # title with one misheard name would otherwise fail 0.92).
                need = min(min_match, 1 - 2 / max(1, seg['words']))
                if match is None or match >= need:
                    break
                # The same score twice means Whisper and the text disagree the
                # same way every time (a name, a number); new seeds won't help.
                if any(a['match'] == match for a in attempts[:-1]):
                    print(f'  Segment {i}: match {match:.3f} repeated, keeping the best take')
                    break
                if n < max_attempts:
                    print(f'  Segment {i}: match {match:.3f} below {need:.2f}, retrying with a new seed')
            scored = [a for a in attempts if a['match'] is not None]
            best   = max(scored, key=lambda a: a['match']) if scored else attempts[-1]
            for a in attempts:
                if a is not best:
                    _rm(a['path'])
            paths.append(best['path'])
            seg_log.append({'kind': seg['kind'], 'words': seg['words'], 'attempts': len(attempts),
                            'seed': best['seed'], 'match': best['match'], 'seconds': best['seconds'],
                            'text': seg['text'][:90]})
            m = f', match {best["match"]:.3f}' if best['match'] is not None else ''
            print(f'  Segment {i}/{len(segs)} [{seg["kind"]}] {seg["words"]} words, '
                  f'{best["seconds"]:.0f}s{m}', flush=True)
    finally:
        if worker:
            worker.close()

    # 5. join
    gaps = [gap_after(s, segs[k + 1] if k + 1 < len(segs) else None, preset, cfg)
            for k, s in enumerate(segs)]
    end_pause = parsed['end_pause_ms'] / 1000.0 if parsed['end_pause_ms'] else 0.0
    dest = os.path.join(get_audio_folder(), f'{slug}.mp3')
    try:
        assemble(paths, gaps, end_pause, dest, os.path.join(get_temp_folder(), f'breeze-{slug}'))
    finally:
        for p in paths:
            _rm(p)

    matches = [s['match'] for s in seg_log if s['match'] is not None]
    stats = {
        'slug': slug, 'genre': preset['genre'], 'genre_method': info['genre_method'],
        'tone': info['tone'], 'quote_method': info['quote_method'],
        'segments': len(segs), 'quotes': n_quotes, 'words': sum(s['words'] for s in segs),
        'gen_seconds': round(gen_total, 1), 'text_model_seconds': info['llm_seconds'],
        'wall_seconds': round(time.time() - started, 1),
        'match_mean': round(sum(matches) / len(matches), 4) if matches else None,
        'match_min': round(min(matches), 4) if matches else None,
        'narrator': preset['narrator'], 'narrator_guidance': preset['narrator_guidance'],
        'quote': preset['quote'], 'quote_guidance': preset['quote_guidance'],
        'quote_cue': preset.get('quote_cue', ''), 'temperature': preset['temperature'],
        'voice': voice_file, 'voice_seconds': round(ref_secs, 1),
    }
    with open(os.path.join(get_temp_folder(), f'{slug}.breeze.json'), 'w', encoding='utf-8') as f:
        json.dump({**stats, 'segments_detail': seg_log,
                   'quote_sentences': [s['text'] for s in sentences if s['quote']]},
                  f, indent=1, ensure_ascii=False)
    log_ai_stats('breeze-articles.csv', {
        'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        **{k: (('/'.join(v) if isinstance(v, list) else v) if v is not None else '')
           for k, v in stats.items() if k not in ('narrator', 'quote')}})
    log_ai_detail('breeze-details.jsonl', {'slug': slug, 'segments': seg_log,
                                           'errors': info['errors']})
    return 'success', f'{len(segs)} segment(s), genre {preset["genre"]}', stats


def _rm(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass
