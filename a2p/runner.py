"""Runs the queue: one job at a time, in a background thread.

For an article: pick the voice and engine, let the engine plan the
segments, generate each one (with the Whisper check and retries), then
join, normalize, tag and file the MP3.

Stop and resume: every finished segment is saved and recorded. Stop
cancels only the segment in progress and leaves the job "stopped"; running
it again generates just the missing segments, as long as the text, voice,
engine and their settings are unchanged. Otherwise it starts over.
"""
import hashlib, json, os, random, shutil, threading, time
from . import art, audio, cleanup, comfy, comfyhost, db, downloads, engines, fetch, llm, settings, stats, voices

_thread   = None
_lock     = threading.Lock()
state     = {'processing': False, 'job_id': None, 'last_engine': ''}
_shuffle  = {}        # job id -> (pick, voice) so a shuffled voice is chosen once per run


# ---- planning -------------------------------------------------------------
def choose(job):
    """(voice, engine id, reason) for a job."""
    pick = job.get('voice') or ''
    memo = _shuffle.get(job['id'])
    if memo and memo[0] == pick:
        voice = memo[1]
    else:
        voice = voices.resolve(pick)
        _shuffle[job['id']] = (pick, voice)
    engine, reason = engines.resolve(job.get('engine') or '', voice or '')
    return voice, engine, reason


def _signature(job, voice, engine_id):
    """Hash of everything that decides how the article sounds."""
    s = settings.load()
    parts = {
        'title': job['title'], 'author': job['author'], 'text': job['text'],
        'voice': voice, 'engine': engine_id, 'engine_settings': s['engines'].get(engine_id),
        'genres': s['genres'], 'default_genre': s['default_genre'], 'text_model': s['text_model'],
        'voice_entry': settings.voice_entry(voice),
        'site_entry': settings.site_entry(job.get('url') or '', job.get('site') or ''),
        'pronunciations': s['pronunciations'],
    }
    return hashlib.sha1(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def _plan(job, voice, engine, log):
    """The engine's plan, reused from a stopped run when nothing changed."""
    sig  = _signature(job, voice, engine.id)
    path = os.path.join(fetch.job_dir(job['id']), 'plan.json')
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            if saved.get('signature') == sig:
                return saved['plan'], sig, True
        except Exception:
            pass
    plan = engine.plan(job, voice, log)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'signature': sig, 'plan': plan}, f, ensure_ascii=False)
    return plan, sig, False


# ---- one job --------------------------------------------------------------
def run_article(job, whisper_box):
    """Returns (status, error): status is done | failed | stopped."""
    job_id = job['id']
    log    = lambda msg: db.log(job_id, msg)
    s      = settings.load()

    voice, engine_id, reason = choose(job)
    if not voice:
        return 'failed', f'No voice sample MP3 found in {s["voices_dir"]}.'
    if not engine_id:
        return 'failed', 'No engine workflow found in the workflows folder (breeze.json or vibevoice.json).'
    engine = engines.ALL[engine_id]
    db.update_job(job_id, voice_used=voice, engine_used=engine_id)
    log(f'Voice {voice}, engine {engine.label} ({reason}).')
    _switch_engine(engine_id)

    # Everything that needs the text model happens first, back to back, so it
    # loads once: clean-up, then the engine's plan (genre and quotes), then the
    # art prompt. Then cover art, and only then the TTS model.
    started = time.time()
    try:
        if s['cleanup'] and s['text_model']:
            job = cleanup.run(job, log)
        if comfy.stop.is_set():
            return 'stopped', None
        plan, sig, reused = _plan(job, voice, engine, log)
        if comfy.stop.is_set():
            return 'stopped', None
        job = art.run(job, log)
    except Exception as e:
        return ('stopped', None) if comfy.stop.is_set() else ('failed', f'Could not prepare the article: {e}')
    if comfy.stop.is_set():
        return 'stopped', None
    if llm.loaded:
        comfy.free()                     # hand the text model's memory back before TTS loads
        llm.loaded = False
    rows = db.set_plan(job_id, sig, plan['segments'])
    have = sum(1 for r in rows if r['path'])
    if have:
        log(f'Resuming: {have} of {len(rows)} segments are already done.')

    # Whisper check (one helper process for the whole run)
    whisper = None
    if s['quality_check']:
        if whisper_box.get('w') is None and not whisper_box.get('failed'):
            w = voices.Whisper()
            log('Loading the speech model for the quality check...')
            if w.start():
                whisper_box['w'] = w
            else:
                whisper_box['failed'] = True
                log(f'Quality check off for this run: {w.error}')
        whisper = whisper_box.get('w')
    max_attempts = max(1, int(s['quality']['max_attempts'])) if whisper else 1
    min_match    = float(s['quality']['min_match'])
    work         = fetch.job_dir(job_id)

    for row in rows:
        if row['path']:
            continue
        idx, seg = row['idx'], plan['segments'][row['idx']]
        words, attempts = max(1, row['words']), []
        for n in range(1, max_attempts + 1):
            seed = engine.first_seed() if n == 1 else random.randint(1, 2**31 - 1)
            res, path = engine.render(plan, seg, idx, seed, os.path.join(work, f'j{job_id}_s{idx:03d}_a{n}'))
            if res.note:
                db.log(job_id, res.note)
            if not res.ok:
                for a in attempts:
                    db._remove(a['path'])
                if res.status == 'cancelled':
                    return 'stopped', None
                return 'failed', f'Segment {idx + 1}: {res.detail}'
            match = whisper.score(path, seg['text']).get('match') if whisper else None
            attempts.append({'seed': seed, 'path': path, 'match': match, 'seconds': res.seconds})
            # Short segments get two misheard words of slack (a ten-word title
            # with one unusual name would otherwise always fail).
            need = min(min_match, 1 - 2 / words)
            if match is None or match >= need:
                break
            # The same score twice means Whisper and the text disagree the same
            # way every time (a name, a number): new seeds will not help.
            if any(a['match'] == match for a in attempts[:-1]):
                break
        scored = [a for a in attempts if a['match'] is not None]
        best   = max(scored, key=lambda a: a['match']) if scored else attempts[-1]
        for a in attempts:
            if a is not best:
                db._remove(a['path'])
        secs = sum(a['seconds'] for a in attempts)
        db.finish_segment(job_id, idx, best['path'], best['seed'], best['match'], len(attempts), secs)
        stats.record_take(job, voice, engine_id, seg['kind'], row['words'], len(attempts),
                          attempts[0]['match'], best['match'], secs)
        m = f', match {best["match"]:.3f}' if best['match'] is not None else ''
        t = f', {len(attempts)} takes' if len(attempts) > 1 else ''
        log(f'Segment {idx + 1}/{len(rows)} [{seg["kind"]}] {row["words"]} words, {secs:.0f}s{m}{t}')

    # join -> normalize -> tag -> output folder
    rows = db.segments(job_id)
    wav, mp3 = os.path.join(work, 'joined.wav'), os.path.join(work, 'final.mp3')
    try:
        audio.join([r['path'] for r in rows], [r['gap'] for r in rows], plan.get('end_pause', 0.0),
                   wav, os.path.join(work, 'join'), trim=engine.trim)
        note = audio.encode(wav, mp3, normalize=s['normalize'])
        dest = audio.publish(db.get_job(job_id), mp3)
    except Exception as e:
        return 'failed', str(e)
    matches = [r['match'] for r in rows if r['match'] is not None]
    db.merge_info(job_id, **plan.get('info', {}), segments=len(rows),
                  words=sum(r['words'] for r in rows),
                  generate_seconds=round(sum(r['seconds'] or 0 for r in rows), 1),
                  total_seconds=round(time.time() - started, 1),
                  match_mean=round(sum(matches) / len(matches), 4) if matches else None,
                  match_min=round(min(matches), 4) if matches else None,
                  retried_segments=sum(1 for r in rows if (r['attempts'] or 1) > 1))
    db.update_job(job_id, output_path=dest)
    log(f'Done ({note}): {dest}')
    db.clear_segments(job_id)
    for leftover in (wav, os.path.join(work, 'plan.json')):      # the cover art stays
        db._remove(leftover)
    shutil.rmtree(os.path.join(work, 'join'), ignore_errors=True)
    return 'done', None


def run_download(job):
    """YouTube, JW Player and embedded audio: download, tag, file. No voice
    engine, no normalizing. Returns (status, error)."""
    job_id, started = job['id'], time.time()
    log = lambda msg: db.log(job_id, msg)
    log('Downloading the audio...')
    try:
        mp3  = downloads.fetch(job, fetch.job_dir(job_id), log)
        dest = audio.publish(db.get_job(job_id), mp3)
    except downloads.Cancelled:
        return 'stopped', None
    except Exception as e:
        return 'failed', str(e)
    db.merge_info(job_id, total_seconds=round(time.time() - started, 1))
    db.update_job(job_id, output_path=dest)
    log(f'Done: {dest}')
    return 'done', None


def _switch_engine(engine_id):
    """Unload the previous engine's model before a different engine runs,
    unless ComfyUI is busy with someone else's jobs."""
    last = state['last_engine']
    if last and last != engine_id:
        if comfy.queue_empty():
            db.log(None, f'Switching from {engines.ALL[last].label} to {engines.ALL[engine_id].label}: '
                         f'freeing ComfyUI memory.')
            comfy.free()
        else:
            db.log(None, 'Switching engine while ComfyUI is busy with other jobs: not freeing memory.')
    state['last_engine'] = engine_id


def run_job(job_id, whisper_box):
    job = db.get_job(job_id)
    db.update_job(job_id, status='processing', error=None, started_at=time.time())
    state['job_id'] = job_id
    try:
        if job['kind'] == 'article':
            status, error = run_article(job, whisper_box)
        else:
            status, error = run_download(job)
    except Exception as e:                                  # never let one job kill the queue
        status, error = 'failed', f'{type(e).__name__}: {e}'
    finally:
        state['job_id'] = None
    if status == 'failed':
        db.log(job_id, f'Failed: {error}')
    elif status == 'stopped':
        db.log(job_id, 'Stopped. Finished segments are kept; run it again to resume.' if job['kind'] == 'article' else 'Stopped.')
    db.update_job(job_id, status=status, error=error,
                  finished_at=time.time() if status == 'done' else None)
    if status in ('done', 'failed'):
        try:
            stats.record_run(job, status, error)
        except Exception as e:                              # statistics must never fail a job
            db.log(job_id, f'Could not record statistics: {e}')
    return status


# ---- the queue ------------------------------------------------------------
def pick_next(pending):
    """Queue order, except that with group_by_engine an article for the
    engine already loaded (or a download, which needs none) goes first."""
    last = state['last_engine']
    if not last or not settings.load()['group_by_engine']:
        return pending[0]
    for job in pending:
        if job['kind'] != 'article' or choose(job)[1] == last:
            return job
    return pending[0]


def run_queue():
    """Process every pending job, then return. Blocks; use start() for a thread."""
    with _lock:
        if state['processing']:
            return
        state['processing'] = True
    comfy.stop.clear()
    state['last_engine'] = ''
    _shuffle.clear()
    whisper_box = {}
    started_comfy, tried_start = False, False
    try:
        while not comfy.stop.is_set():
            pending = db.jobs('pending')
            articles = any(j['kind'] == 'article' for j in pending)
            if articles and not comfy.running() and not tried_start \
                    and settings.load()['comfy']['start_when_needed']:
                tried_start = True
                ok, error = comfyhost.start()
                started_comfy = ok and comfyhost.ours()
                if not ok:
                    db.log(None, f'Could not start ComfyUI: {error}')
                continue
            if not comfy.running():                     # downloads don't need ComfyUI; articles wait
                pending = [j for j in pending if j['kind'] != 'article']
                if not pending:
                    if articles:
                        db.log(None, f'ComfyUI is not running at {comfy.url()}: articles are left queued.')
                    break
            if not pending:
                break
            run_job(pick_next(pending)['id'], whisper_box)
    finally:
        if whisper_box.get('w'):
            whisper_box['w'].close()
        if started_comfy and comfyhost.ours():
            comfyhost.stop()             # we opened it for this run, so we close it
        elif state['last_engine'] and comfy.queue_empty():
            comfy.free()                 # don't leave our models in a shared ComfyUI
        state['processing'] = False


def start():
    """Start the queue in the background; also re-queues stopped and failed jobs."""
    global _thread
    if state['processing']:
        return False
    for job in db.jobs(('stopped', 'failed', 'processing')):
        db.update_job(job['id'], status='pending', error=None)
    _thread = threading.Thread(target=run_queue, daemon=True)
    _thread.start()
    return True


def stop():
    """Cancel the segment in progress and halt the queue. Finished segments stay."""
    comfy.stop.set()


PREVIEW_TEXT = ('The committee met for three hours on Tuesday and left without a decision. '
                '\u201cWe are closer than we were a week ago, but nobody should expect a vote before '
                'the end of the month,\u201d the chairman told reporters afterwards.')


# The latest voice preview, so the page can show progress and play the
# result even if it was closed or the phone locked while it was being made.
preview_state = {'status': '', 'voice': '', 'engine': '', 'genre': '', 'error': '',
                 'started': None, 'finished': None}


def preview_file(voice):
    return os.path.join(settings.DATA_DIR, 'preview', settings.name_key(voice) + '.mp3')


def start_preview(voice, engine_id='', genre='', text='', overrides=None):
    """Make a preview in the background. Returns (ok, error). With
    overrides, the preview uses those settings (the page's unsaved edits)
    instead of the saved ones; nothing is written."""
    if overrides is not None:
        try:
            settings.check(overrides)
        except settings.SettingsError as e:
            return False, f'Those settings are not valid: {e}'
    if preview_state['status'] == 'running':
        return False, f'A preview for {preview_state["voice"]} is still being made.'
    if state['processing']:
        return False, 'The queue is running; try the preview when it is idle.'
    if not comfy.running():
        return False, f'ComfyUI is not running at {comfy.url()}.'
    preview_state.update(status='running', voice=voice, engine=engine_id, genre=genre, error='',
                         started=time.time(), finished=None, unsaved=overrides is not None)
    db._touch()

    def work():
        if overrides is None:
            path, error = preview(voice, engine_id, genre, text)
        else:
            with settings.trial(overrides):
                path, error = preview(voice, engine_id, genre, text)
        preview_state.update(status='failed' if error else 'done', error=error or '', finished=time.time())
        db._touch()
    threading.Thread(target=work, daemon=True).start()
    return True, None


def preview(voice, engine_id='', genre='', text=''):
    """Render a short sample with a voice's current settings, so they can be
    tuned by ear without running an article. Returns (path, error). Uses the
    quotation-mark rule for quotes (no text model) and the genre given.
    The result is kept per voice (preview_file)."""
    if state['processing']:
        return None, 'The queue is running; try the preview when it is idle.'
    if not comfy.running():
        return None, f'ComfyUI is not running at {comfy.url()}.'
    voice = voices.resolve(voice)
    engine_id = engines.normalize(engine_id) or engines.resolve('', voice or '')[0]
    if not voice or engine_id not in engines.available():
        return None, 'No voice sample or engine workflow found.'
    engine = engines.ALL[engine_id]
    _switch_engine(engine_id)             # same as the queue: unload the other engine's model first
    job = {'id': 0, 'title': '', 'author': '', 'site': '', 'url': '', 'genre': genre,
           'text': (text or PREVIEW_TEXT).strip(), 'skip_text_model': True}
    out_dir = os.path.join(settings.DATA_DIR, 'preview')
    os.makedirs(out_dir, exist_ok=True)
    comfy.stop.clear()
    with _lock:
        state['processing'] = True
    try:
        plan = engine.plan(job, voice, lambda m: None)
        paths = []
        for i, seg in enumerate(plan['segments'][:3]):
            res, path = engine.render(plan, seg, i, engine.first_seed(),
                                      os.path.join(out_dir, f'preview_{int(time.time())}_{i}'))
            if not res.ok:
                return None, res.detail
            paths.append((path, seg.get('gap', 0.0)))
        wav = os.path.join(out_dir, 'preview.wav')
        mp3 = preview_file(voice)
        audio.join([p for p, _ in paths], [g for _, g in paths], 0.0, wav,
                   os.path.join(out_dir, 'join'), trim=engine.trim)
        audio.encode(wav, mp3, normalize=settings.load()['normalize'])
        for p, _ in paths:
            db._remove(p)
        db._remove(wav)
        return mp3, None
    except Exception as e:
        return None, str(e)
    finally:
        state['processing'] = False


def start_over(job_id):
    """Forget a job's finished segments so the next run begins from scratch."""
    db.clear_segments(job_id)
    shutil.rmtree(os.path.join(settings.WORK_DIR, str(job_id), 'join'), ignore_errors=True)
    plan = os.path.join(settings.WORK_DIR, str(job_id), 'plan.json')
    if os.path.isfile(plan):
        os.remove(plan)
    db.update_job(job_id, status='pending', error=None)
