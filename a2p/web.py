"""The web page's server: a small JSON API over the database and runner.

Everything the page shows comes from /api/state; /api/events pushes a
fresh state whenever the database changes, so the page never polls.
"""
import io, json, os, time
from flask import Flask, Response, jsonify, request, send_file
from . import audio, comfy, comfyhost, db, engines, fetch, runner, settings, stats, textclean, voices

HERE = os.path.dirname(os.path.abspath(__file__))
app  = Flask(__name__, static_folder=os.path.join(HERE, 'static'), static_url_path='/static')
app.json.sort_keys = False      # keep genres, sites and voices in the order they were written


def fail(message, code=400, **more):
    return jsonify(dict(error=message, **more)), code


def _uploaded_image():
    """The image a request carries, as bytes: an uploaded file, a JSON body
    with image_data (a data: URL) or image_url (fetched here, for clipboards
    that only hold a link to the picture), or the raw body."""
    upload = request.files.get('image')
    if upload:
        return upload.read()
    data = request.get_json(silent=True) if request.is_json else None
    if isinstance(data, dict):
        if data.get('image_data'):
            import base64
            return base64.b64decode(str(data['image_data']).split(',', 1)[-1])
        url = str(data.get('image_url') or '')
        if url.lower().startswith(('http://', 'https://')):
            return textclean.http_get(url, timeout=20).content
        return b''
    return request.get_data()


# ---- state ----------------------------------------------------------------
def _badge(job, avail):
    """Engine shown on a card: used once it ran, else the one it will use
    ("auto" when a shuffled voice decides)."""
    if job['kind'] != 'article' or not avail:
        return ''
    if job['engine_used'] and job['status'] in ('done', 'processing', 'stopped'):
        return job['engine_used']
    s = settings.load()
    voice = job['voice'] or s['default_voice']
    if voice == 'shuffle':
        if (len(avail) > 1 and not engines.normalize(job['engine'])
                and any(engines.normalize(v.get('engine')) for v in s['voices'].values())):
            return 'auto'
        voice = ''
    return engines.resolve(job['engine'] or '', voice)[0]


def _job_view(job, avail):
    segs = db.segments(job['id']) if job['status'] in ('processing', 'stopped', 'failed') else []
    art  = job['art_path'] if job['art_path'] and os.path.isfile(job['art_path']) else None
    info = job['info']
    return {
        'id': job['id'], 'kind': job['kind'], 'status': job['status'], 'url': job['url'],
        'title': job['title'], 'author': job['author'], 'site': job['site'],
        'voice': job['voice'] or '', 'engine': job['engine'] or '',
        'voice_used': job['voice_used'], 'engine_badge': _badge(job, avail),
        'error': job['error'], 'words': info.get('words'),
        'segments_done': sum(1 for s in segs if s['path']), 'segments_total': len(segs),
        'art': f'/api/jobs/{job["id"]}/art?v={int(os.path.getmtime(art))}' if art else None,
        'has_file': bool(job['output_path'] and os.path.isfile(job['output_path'])),
        'added_at': job['added_at'], 'finished_at': job['finished_at'],
        'genre': info.get('genre'), 'minutes': round(info['total_seconds'] / 60, 1) if info.get('total_seconds') else None,
        'retries': (sum(1 for s in segs if (s['attempts'] or 1) > 1) if segs else info.get('retried_segments')) or 0,
        'current': job['id'] == runner.state['job_id'],
    }


def state():
    avail = engines.available()
    s = settings.load()
    return {
        'jobs': [_job_view(j, avail) for j in db.jobs()],
        'processing': runner.state['processing'],
        'comfy': comfyhost.status(),
        'engines': [{'id': e, 'label': engines.ALL[e].label} for e in avail],
        'default_engine': engines.default(),
        'voices': voices.available(), 'default_voice': s['default_voice'],
        'genres': list(s['genres']),
        'preview': dict(runner.preview_state),
    }


@app.get('/')
def index():
    return send_file(os.path.join(HERE, 'static', 'index.html'))


@app.get('/api/state')
def api_state():
    try:
        return jsonify(state())
    except settings.SettingsError as e:
        return fail(str(e), 500)


@app.get('/api/events')
def api_events():
    """Server-sent events: a new state whenever anything changes (and a
    heartbeat every 15 s so ComfyUI going up or down is noticed)."""
    def stream():
        seen, last = None, 0
        while True:
            now = time.time()
            mark = (db.changes, runner.state['processing'], comfyhost.busy, runner.preview_state['status'])
            if mark != seen or now - last > 15:
                seen, last = mark, now
                try:
                    yield f'data: {json.dumps(state())}\n\n'
                except Exception as e:
                    yield f'data: {json.dumps({"error": str(e)})}\n\n'
            time.sleep(0.5)
    return Response(stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ---- adding and editing jobs ------------------------------------------------
@app.post('/api/add')
def api_add():
    data = request.get_json(force=True, silent=True) or {}
    url, text = (data.get('url') or '').strip(), (data.get('text') or '').strip()
    if not url and not text:
        return fail('Paste a URL or the article text.')
    if url and not url.lower().startswith(('http://', 'https://')):
        return fail('That is not a URL. To add plain text, switch to Text.')
    try:
        return jsonify({'id': fetch.add(url=url, pasted=text)})
    except fetch.NeedsPaste as e:
        return fail(str(e), 409, needs_paste=True)
    except Exception as e:
        return fail(f'Could not add the article: {e}', 500)


def _job_or_404(job_id):
    job = db.get_job(job_id)
    return job, (None if job else fail('No such job.', 404))


@app.patch('/api/jobs/<int:job_id>')
def api_job_edit(job_id):
    """Change the voice, engine, title, author or site of a job."""
    job, err = _job_or_404(job_id)
    if err:
        return err
    data, fields = request.get_json(force=True, silent=True) or {}, {}
    if 'voice' in data:
        fields['voice'] = (data['voice'] or '').strip()
    if 'engine' in data:
        fields['engine'] = engines.normalize(data['engine'])
    meta = {k: str(data[k]).strip() for k in ('title', 'author', 'site') if k in data}
    if 'title' in meta and not meta['title']:
        return fail('The title cannot be empty.')
    changed = {k: v for k, v in meta.items() if v != (job[k] or '')}
    if job['status'] == 'processing' and (fields or changed):
        return fail('This article is being generated; stop it first.', 409)
    if changed:
        fields.update(changed)
        info = job['info']
        info['metadata_edited'] = True            # clean-up will not overwrite these
        fields['info'] = info
        if job['status'] == 'done' and job['output_path'] and os.path.isfile(job['output_path']):
            new = dict(job, **changed)
            fields['output_path'] = audio.retag(job['output_path'], new['title'], new['site'], new['author'])
    if fields:
        db.update_job(job_id, **fields)
    return jsonify({'ok': True})


@app.delete('/api/jobs/<int:job_id>')
def api_job_delete(job_id):
    """Remove a job. ?file=1 also deletes its finished MP3."""
    job, err = _job_or_404(job_id)
    if err:
        return err
    if job['status'] == 'processing':
        return fail('This article is being generated; stop it first.', 409)
    if request.args.get('file') and job['output_path'] and os.path.isfile(job['output_path']):
        os.remove(job['output_path'])
    db.clear_segments(job_id)
    db.delete_job(job_id)
    import shutil
    shutil.rmtree(os.path.join(settings.WORK_DIR, str(job_id)), ignore_errors=True)
    return jsonify({'ok': True})


@app.post('/api/jobs/<int:job_id>/startover')
def api_job_startover(job_id):
    job, err = _job_or_404(job_id)
    if err:
        return err
    if job['status'] == 'processing':
        return fail('This article is being generated; stop it first.', 409)
    runner.start_over(job_id)
    return jsonify({'ok': True})


@app.get('/api/jobs/<int:job_id>/log')
def api_job_log(job_id):
    return jsonify({'log': db.job_log(job_id)})


@app.get('/api/jobs/<int:job_id>/art')
def api_job_art(job_id):
    job = db.get_job(job_id)
    if not job or not job['art_path'] or not os.path.isfile(job['art_path']):
        return fail('No art.', 404)
    return send_file(job['art_path'], mimetype='image/jpeg', max_age=86400)


@app.post('/api/jobs/<int:job_id>/art')
def api_job_art_set(job_id):
    """Replace a job's cover art with an uploaded or pasted image."""
    job, err = _job_or_404(job_id)
    if err:
        return err
    dest = os.path.join(fetch.job_dir(job_id), 'art.jpg')
    try:
        raw = _uploaded_image()
        if not raw:
            return fail('No image received.')
        textclean.crop_image_bytes_to_square(raw).save(dest, 'JPEG', quality=90)
    except Exception as e:
        return fail(f'Could not read that image: {e}')
    db.update_job(job_id, art_path=dest)
    db.merge_info(job_id, art_source='custom')
    if job['status'] == 'done' and job['output_path'] and os.path.isfile(job['output_path']):
        audio.set_art(job['output_path'], dest)
    return jsonify({'ok': True})


@app.get('/api/jobs/<int:job_id>/download')
def api_job_download(job_id):
    job = db.get_job(job_id)
    if not job or not job['output_path'] or not os.path.isfile(job['output_path']):
        return fail('The file is no longer there.', 404)
    return send_file(job['output_path'], as_attachment=True)


# ---- library: every MP3 in the output folder ---------------------------------
_tag_cache = {}       # path -> (mtime, tags)


def _library_path(rel):
    """Absolute path of a library file, refusing anything outside the output folder."""
    root = os.path.abspath(settings.load()['output_dir'])
    path = os.path.abspath(os.path.join(root, rel or ''))
    if os.path.commonpath([root, path]) != root or not path.lower().endswith('.mp3') or not os.path.isfile(path):
        return None
    return path


def library(query=''):
    """Every MP3 under the output folder, newest first, with its tags and
    (when this app made it) the job's details."""
    root  = os.path.abspath(settings.load()['output_dir'])
    by_path = {os.path.normcase(os.path.abspath(j['output_path'])): j
               for j in db.jobs('done') if j['output_path']}
    words = query.lower().split()
    out = []
    for folder, _, files in os.walk(root):
        for name in files:
            if not name.lower().endswith('.mp3'):
                continue
            path  = os.path.join(folder, name)
            mtime = os.path.getmtime(path)
            cached = _tag_cache.get(path)
            if not cached or cached[0] != mtime:
                cached = _tag_cache[path] = (mtime, audio.read_tags(path))
            tags = cached[1]
            hay = f'{name} {tags["title"]} {tags["site"]} {tags["author"]}'.lower()
            if any(w not in hay for w in words):
                continue
            rel = os.path.relpath(path, root).replace('\\', '/')
            job = by_path.get(os.path.normcase(path))
            info = job['info'] if job else {}
            out.append({'path': rel, 'title': tags['title'] or os.path.splitext(name)[0],
                        'site': tags['site'], 'author': tags['author'], 'track': tags['track'],
                        'has_art': tags['has_art'], 'modified': mtime, 'size': os.path.getsize(path),
                        'job_id': job['id'] if job else None, 'kind': job['kind'] if job else None,
                        'engine': job['engine_used'] if job else None,
                        'voice': job['voice_used'] if job else None,
                        'genre': info.get('genre'), 'retries': info.get('retried_segments') or 0,
                        'minutes': round(info['total_seconds'] / 60, 1) if info.get('total_seconds') else None})
    out.sort(key=lambda f: f['modified'], reverse=True)
    return out


@app.get('/api/library')
def api_library():
    files = library(request.args.get('q', ''))
    limit = int(request.args.get('limit', 100))
    return jsonify({'total': len(files), 'files': files[:limit]})


@app.get('/api/library/art')
def api_library_art():
    path = _library_path(request.args.get('path'))
    data = audio.art_bytes(path) if path else None
    if not data:
        return fail('No art.', 404)
    return send_file(io.BytesIO(data), mimetype='image/jpeg', max_age=3600)


@app.get('/api/library/play')
def api_library_play():
    """The MP3 for the page's player (supports seeking)."""
    path = _library_path(request.args.get('path'))
    return send_file(path, mimetype='audio/mpeg', conditional=True) if path else fail('The file is no longer there.', 404)


@app.get('/api/library/download')
def api_library_download():
    path = _library_path(request.args.get('path'))
    return send_file(path, as_attachment=True) if path else fail('The file is no longer there.', 404)


def _sync_job(old_path, new_path=None, **fields):
    """Keep the job that made a file in step with a change to that file."""
    for job in db.jobs('done'):
        if job['output_path'] and os.path.normcase(os.path.abspath(job['output_path'])) == os.path.normcase(old_path):
            if new_path is None:
                db.clear_segments(job['id'])
                db.delete_job(job['id'])
            else:
                db.update_job(job['id'], output_path=new_path, **fields)


@app.patch('/api/library')
def api_library_edit():
    """Retag (and so rename/move) any file in the library."""
    data = request.get_json(force=True, silent=True) or {}
    path = _library_path(data.get('path'))
    if not path:
        return fail('The file is no longer there.', 404)
    title = str(data.get('title') or '').strip()
    if not title:
        return fail('The title cannot be empty.')
    author, site = str(data.get('author') or '').strip(), str(data.get('site') or '').strip()
    new = audio.retag(path, title, site, author)
    _sync_job(path, new, title=title, author=author, site=site)
    _tag_cache.pop(path, None)
    db._touch()
    return jsonify({'ok': True, 'path': os.path.relpath(new, os.path.abspath(settings.load()['output_dir'])).replace('\\', '/')})


@app.post('/api/library/art')
def api_library_art_set():
    path = _library_path(request.args.get('path'))
    if not path:
        return fail('The file is no longer there.', 404)
    tmp = os.path.join(settings.DATA_DIR, 'library-art.jpg')
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    try:
        textclean.crop_image_bytes_to_square(_uploaded_image()).save(tmp, 'JPEG', quality=90)
    except Exception as e:
        return fail(f'Could not read that image: {e}')
    audio.set_art(path, tmp)
    os.remove(tmp)
    _tag_cache.pop(path, None)
    db._touch()
    return jsonify({'ok': True})


@app.delete('/api/library')
def api_library_delete():
    path = _library_path(request.args.get('path'))
    if not path:
        return fail('The file is no longer there.', 404)
    os.remove(path)
    audio.remove_empty_dirs(os.path.dirname(path))
    _sync_job(path)
    _tag_cache.pop(path, None)
    db._touch()
    return jsonify({'ok': True})


# ---- statistics ---------------------------------------------------------------
@app.get('/api/stats')
def api_stats():
    days = request.args.get('days', '')
    return jsonify(stats.summary(int(days) if days.isdigit() and int(days) > 0 else None))


@app.post('/api/stats/import')
def api_stats_import():
    """Bring in the original app's history (re-running replaces the earlier import)."""
    return jsonify(stats.import_old_app())


# ---- running ----------------------------------------------------------------
@app.post('/api/run')
def api_run():
    waiting = db.jobs(('pending', 'stopped', 'failed'))
    if (not comfy.running() and not settings.load()['comfy']['start_when_needed']
            and all(j['kind'] == 'article' for j in waiting)):
        return fail(f'ComfyUI is not running at {comfy.url()}. Start ComfyUI first.', 409)
    if not runner.start():
        return fail('Already running.', 409)
    return jsonify({'ok': True})


@app.post('/api/stop')
def api_stop():
    runner.stop()
    return jsonify({'ok': True})


@app.post('/api/comfy/start')
def api_comfy_start():
    """Launch ComfyUI in the background; the page follows it through /api/events."""
    if comfy.running():
        return fail('ComfyUI is already running.', 409)
    if comfyhost.busy:
        return fail(f'ComfyUI is already {comfyhost.busy}.', 409)
    try:
        comfyhost.command()                       # fail now, with a reason, if it can't be launched
    except RuntimeError as e:
        return fail(str(e), 409)
    import threading
    comfy.stop.clear()
    threading.Thread(target=comfyhost.start, daemon=True).start()
    return jsonify({'ok': True})


@app.post('/api/comfy/stop')
def api_comfy_stop():
    if runner.state['processing']:
        return fail('The queue is running; stop it first.', 409)
    ok, error = comfyhost.stop()
    return jsonify({'ok': True}) if ok else fail(error, 409)


@app.post('/api/free')
def api_free():
    if runner.state['processing']:
        return fail('The queue is running.', 409)
    return jsonify({'ok': comfy.free()})


# ---- settings ---------------------------------------------------------------
def _overrides():
    if not os.path.isfile(settings.FILE):
        return {}
    with open(settings.FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


@app.get('/api/settings')
def api_settings():
    """The saved overrides, the built-in defaults, and the merged result."""
    try:
        return jsonify({'overrides': _overrides(), 'defaults': settings.DEFAULTS,
                        'merged': settings.load()})
    except (settings.SettingsError, json.JSONDecodeError) as e:
        with open(settings.FILE, 'r', encoding='utf-8') as f:
            return jsonify({'error': str(e), 'raw': f.read(), 'defaults': settings.DEFAULTS})


@app.put('/api/settings')
def api_settings_save():
    """Replace the overrides. Nothing is written unless the result validates."""
    new = request.get_json(force=True, silent=True)
    if not isinstance(new, dict):
        return fail('Settings must be a JSON object.')
    try:
        merged = settings._merge(json.loads(json.dumps(settings.DEFAULTS)), new)
        problems = list(settings.validate(merged))
    except Exception as e:
        problems = [str(e)]
    if problems:
        return fail(problems[0].replace('settings.json: ', ''))
    settings.save(new)
    db._touch()
    return jsonify({'ok': True})


@app.post('/api/preview')
def api_preview():
    """Start a short spoken sample for a voice with its current settings.
    It is made in the background; the page follows preview_state in
    /api/events and then plays /api/preview/audio."""
    data = request.get_json(force=True, silent=True) or {}
    ok, error = runner.start_preview(data.get('voice') or '', data.get('engine') or '',
                                     data.get('genre') or '', data.get('text') or '',
                                     data.get('settings'))
    return (jsonify({'ok': True}), 202) if ok else fail(error, 409)


@app.get('/api/preview/audio')
def api_preview_audio():
    """The latest preview made for a voice."""
    path = runner.preview_file(request.args.get('voice') or '')
    if not os.path.isfile(path):
        return fail('No preview yet.', 404)
    return send_file(path, mimetype='audio/mpeg', max_age=0)


def serve():
    port = int(settings.load()['port'])
    print(f'Article2Pod (new) on http://localhost:{port}')
    app.run(host='0.0.0.0', port=port, threaded=True, use_reloader=False)
