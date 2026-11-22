"""The web API, with ComfyUI and the network stubbed out."""
import io, json
import pytest
from a2p import db, engines, fetch, runner, settings, web
from test_core import conf     # noqa: F401  (fixture)

ARTICLE = {'kind': 'article', 'title': 'A Headline', 'author': 'Jo Bloggs', 'site': 'Example News', 'image_url': None,
           'text': '\n'.join(f'Paragraph {i} with several words of real reporting in it.' for i in range(6))}


@pytest.fixture
def client(conf, monkeypatch, tmp_path):
    conf(output_dir=str(tmp_path / 'out'), voices_dir=str(tmp_path / 'voices'))
    monkeypatch.setattr(settings, 'WORK_DIR', str(tmp_path / 'work'))
    monkeypatch.setattr(web.comfy, 'running', lambda: True)
    monkeypatch.setattr(fetch, 'from_url', lambda url: dict(ARTICLE))
    for eid, e in engines.ALL.items():
        monkeypatch.setattr(e, 'available', lambda: True)
    web.app.config['TESTING'] = True
    return web.app.test_client()


def add(client, **body):
    return client.post('/api/add', json=body or {'url': 'https://example.com/a'})


def test_add_and_state(client):
    r = add(client)
    assert r.status_code == 200
    s = client.get('/api/state').get_json()
    job = s['jobs'][0]
    assert job['title'] == 'A Headline' and job['status'] == 'pending' and job['engine_badge'] == 'breeze'
    assert {e['id'] for e in s['engines']} == {'vibevoice', 'breeze'} and s['comfy']['running'] is True

def test_add_rejects_non_urls_and_empty(client):
    assert add(client, url='not a url').status_code == 400
    assert client.post('/api/add', json={}).status_code == 400

def test_blocked_site_asks_for_paste(client, monkeypatch):
    def blocked(url):
        raise fetch.NeedsPaste('This site blocks scraping.')
    monkeypatch.setattr(fetch, 'from_url', blocked)
    r = add(client)
    assert r.status_code == 409 and r.get_json()['needs_paste'] is True

def test_edit_voice_engine_and_metadata(client):
    job_id = add(client).get_json()['id']
    r = client.patch(f'/api/jobs/{job_id}', json={'voice': 'Bill.mp3', 'engine': 'VibeVoice', 'title': 'Better Title',
                                                  'author': 'Jo Bloggs', 'site': 'Example News'})
    assert r.status_code == 200
    job = db.get_job(job_id)
    assert (job['voice'], job['engine'], job['title']) == ('Bill.mp3', 'vibevoice', 'Better Title')
    assert job['info']['metadata_edited'] is True                       # clean-up must leave it alone
    assert client.get('/api/state').get_json()['jobs'][0]['engine_badge'] == 'vibevoice'
    assert client.patch(f'/api/jobs/{job_id}', json={'title': ' '}).status_code == 400

def test_voice_only_edit_does_not_mark_metadata_edited(client):
    job_id = add(client).get_json()['id']
    client.patch(f'/api/jobs/{job_id}', json={'voice': 'shuffle', 'title': 'A Headline'})
    assert 'metadata_edited' not in db.get_job(job_id)['info']

def test_processing_job_is_locked(client):
    job_id = add(client).get_json()['id']
    db.update_job(job_id, status='processing')
    assert client.patch(f'/api/jobs/{job_id}', json={'voice': 'x.mp3'}).status_code == 409
    assert client.delete(f'/api/jobs/{job_id}').status_code == 409
    assert client.post(f'/api/jobs/{job_id}/startover').status_code == 409

def test_delete_and_missing(client):
    job_id = add(client).get_json()['id']
    assert client.delete(f'/api/jobs/{job_id}').status_code == 200
    assert db.get_job(job_id) is None
    assert client.delete(f'/api/jobs/{job_id}').status_code == 404

def test_art_upload(client):
    from PIL import Image
    job_id = add(client).get_json()['id']
    buf = io.BytesIO()
    Image.new('RGB', (800, 400), 'red').save(buf, 'PNG')
    r = client.post(f'/api/jobs/{job_id}/art', data={'image': (io.BytesIO(buf.getvalue()), 'x.png')})
    assert r.status_code == 200
    job = db.get_job(job_id)
    assert Image.open(job['art_path']).size == (500, 500) and job['info']['art_source'] == 'custom'
    assert client.get(f'/api/jobs/{job_id}/art').status_code == 200
    assert client.post(f'/api/jobs/{job_id}/art', data=b'not an image').status_code == 400

def test_startover_requeues(client):
    job_id = add(client).get_json()['id']
    db.update_job(job_id, status='stopped')
    db.set_plan(job_id, 'p', [{'kind': 'narr', 'text': 'x', 'gap': 0}])
    assert client.post(f'/api/jobs/{job_id}/startover').status_code == 200
    assert db.get_job(job_id)['status'] == 'pending' and db.segments(job_id) == []

def test_run_needs_comfyui_only_when_it_may_not_start_it(client, monkeypatch, conf):
    started = []
    monkeypatch.setattr(runner, 'start', lambda: started.append(1) or True)
    monkeypatch.setattr(web.comfy, 'running', lambda: False)
    add(client)
    assert client.post('/api/run').status_code == 200 and started          # the queue will start ComfyUI itself
    settings.save(dict(json.load(open(settings.FILE)), comfy={'start_when_needed': False}))
    assert client.post('/api/run').status_code == 409

def test_run_and_stop(client, monkeypatch):
    started = []
    monkeypatch.setattr(runner, 'start', lambda: started.append(1) or True)
    assert client.post('/api/run').status_code == 200 and started
    assert client.post('/api/stop').status_code == 200 and web.comfy.stop.is_set()
    web.comfy.stop.clear()

def test_settings_roundtrip_and_validation(client):
    got = client.get('/api/settings').get_json()
    assert 'defaults' in got and got['merged']['port'] == 8080
    new = dict(got['overrides'], default_voice='Bill.mp3',
               voices={'Bill.mp3': {'engine': 'vibevoice', 'breeze': {'guidance': 1.0}}})
    assert client.put('/api/settings', json=new).status_code == 200
    assert settings.load(force=True)['voices']['Bill.mp3']['breeze']['guidance'] == 1.0

    bad = dict(new, voices={'Bill.mp3': {'breeze': {'guidance:': 1.0}}})
    r = client.put('/api/settings', json=bad)
    assert r.status_code == 400 and 'guidance:' in r.get_json()['error']
    assert settings.load(force=True)['voices']['Bill.mp3']['breeze'] == {'guidance': 1.0}   # nothing was written
    assert client.put('/api/settings', json=[1, 2]).status_code == 400

def test_broken_settings_file_is_reported_not_fatal(client):
    with open(settings.FILE, 'w') as f:
        f.write('{ "port": 8080, }')
    settings._cache.update(mtime=None, value=None)
    got = client.get('/api/settings').get_json()
    assert 'error' in got and 'raw' in got
    assert client.get('/api/state').status_code == 500

def test_preview_refused_while_running(client):
    runner.state['processing'] = True
    try:
        assert client.post('/api/preview', json={'voice': 'x.mp3'}).status_code == 409
    finally:
        runner.state['processing'] = False

def test_index_and_static(client):
    assert b'Article2Pod' in client.get('/').data
    assert client.get('/static/app.js').status_code == 200


# ---- library -----------------------------------------------------------------
def _mp3(path, title, site, author):
    import os, subprocess
    from a2p import audio
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono', '-t', '0.2',
                    '-codec:a', 'libmp3lame', path], check=True)
    audio.tag(path, title, site, author, 1)
    return path

@pytest.fixture
def lib(client, tmp_path):
    import os
    out = str(tmp_path / 'out')
    old = _mp3(os.path.join(out, 'Old Site', 'Ann Author', 'Old Site - Made By The Old App.mp3'),
               'Made By The Old App', 'Old Site', 'Ann Author')
    mine = _mp3(os.path.join(out, 'Example News', 'Jo Bloggs', 'Example News - A Headline.mp3'),
                'A Headline', 'Example News', 'Jo Bloggs')
    job_id = add(client).get_json()['id']
    db.update_job(job_id, status='done', output_path=mine, engine_used='breeze', voice_used='Bill.mp3',
                  info={'genre': 'news', 'total_seconds': 120, 'match_mean': 0.98})
    return {'client': client, 'out': out, 'old': old, 'mine': mine, 'job': job_id}

def test_library_lists_every_mp3_with_job_details(lib):
    got = lib['client'].get('/api/library').get_json()
    assert got['total'] == 2
    by_title = {f['title']: f for f in got['files']}
    assert by_title['Made By The Old App']['job_id'] is None and by_title['Made By The Old App']['site'] == 'Old Site'
    mine = by_title['A Headline']
    assert (mine['job_id'], mine['engine'], mine['genre'], mine['minutes']) == (lib['job'], 'breeze', 'news', 2.0)
    assert lib['client'].get('/api/library?q=old+ann').get_json()['total'] == 1
    assert lib['client'].get('/api/library?q=nothing-matches').get_json()['total'] == 0

def test_library_edit_moves_the_file_and_updates_its_job(lib):
    import os
    c = lib['client']
    r = c.patch('/api/library', json={'path': 'Example News/Jo Bloggs/Example News - A Headline.mp3',
                                      'title': 'Better Headline', 'author': 'Jo Bloggs', 'site': 'New Site'})
    assert r.status_code == 200 and r.get_json()['path'] == 'New Site/Jo Bloggs/New Site - Better Headline.mp3'
    new = os.path.join(lib['out'], 'New Site', 'Jo Bloggs', 'New Site - Better Headline.mp3')
    assert os.path.isfile(new) and not os.path.exists(os.path.join(lib['out'], 'Example News'))   # empty folders tidied
    job = db.get_job(lib['job'])
    assert job['output_path'] == new and job['title'] == 'Better Headline' and job['site'] == 'New Site'
    from a2p import audio
    assert audio.read_tags(new)['title'] == 'Better Headline'
    assert c.patch('/api/library', json={'path': 'New Site/Jo Bloggs/New Site - Better Headline.mp3', 'title': ''}).status_code == 400

def test_library_delete_removes_file_and_job(lib):
    import os
    c = lib['client']
    assert c.delete('/api/library?path=Example News/Jo Bloggs/Example News - A Headline.mp3').status_code == 200
    assert not os.path.exists(lib['mine']) and db.get_job(lib['job']) is None
    assert c.delete('/api/library?path=Old Site/Ann Author/Old Site - Made By The Old App.mp3').status_code == 200
    assert c.get('/api/library').get_json()['total'] == 0

def test_library_art_and_download(lib):
    from PIL import Image
    c, rel = lib['client'], 'Old Site/Ann Author/Old Site - Made By The Old App.mp3'
    assert c.get(f'/api/library/art?path={rel}').status_code == 404
    buf = io.BytesIO()
    Image.new('RGB', (300, 900), 'blue').save(buf, 'JPEG')
    assert c.post(f'/api/library/art?path={rel}', data={'image': (io.BytesIO(buf.getvalue()), 'a.jpg')}).status_code == 200
    art = c.get(f'/api/library/art?path={rel}')
    assert art.status_code == 200 and Image.open(io.BytesIO(art.data)).size == (500, 500)
    assert c.get(f'/api/library/download?path={rel}').status_code == 200

def test_library_refuses_paths_outside_the_output_folder(lib, tmp_path):
    c = lib['client']
    secret = _mp3(str(tmp_path / 'elsewhere' / 'secret.mp3'), 'S', 'S', 'S')
    for rel in ('../elsewhere/secret.mp3', secret, 'Old Site/Ann Author', '../../x.mp3'):
        assert c.get(f'/api/library/download?path={rel}').status_code == 404
        assert c.delete(f'/api/library?path={rel}').status_code == 404
    import os
    assert os.path.isfile(secret)


def test_art_from_a_pasted_link_or_data_url(client, monkeypatch):
    import base64
    from PIL import Image
    job_id = add(client).get_json()['id']
    buf = io.BytesIO()
    Image.new('RGB', (640, 480), 'green').save(buf, 'PNG')
    data_url = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    assert client.post(f'/api/jobs/{job_id}/art', json={'image_data': data_url}).status_code == 200
    assert Image.open(db.get_job(job_id)['art_path']).size == (500, 500)
    fetched = []
    monkeypatch.setattr(web.textclean, 'http_get',
                        lambda url, **k: fetched.append(url) or type('R', (), {'content': buf.getvalue()})())
    assert client.post(f'/api/jobs/{job_id}/art', json={'image_url': 'https://example.com/pic.png'}).status_code == 200
    assert fetched == ['https://example.com/pic.png']
    assert client.post(f'/api/jobs/{job_id}/art', json={'image_url': 'file:///c:/secret.png'}).status_code == 400

def test_library_play_supports_seeking(lib):
    c, rel = lib['client'], 'Old Site/Ann Author/Old Site - Made By The Old App.mp3'
    whole = c.get(f'/api/library/play?path={rel}')
    assert whole.status_code == 200 and whole.mimetype == 'audio/mpeg'
    part = c.get(f'/api/library/play?path={rel}', headers={'Range': 'bytes=0-99'})
    assert part.status_code == 206 and len(part.data) == 100
    assert c.get('/api/library/play?path=../x.mp3').status_code == 404


def test_preview_runs_in_the_background_and_is_kept_per_voice(client, monkeypatch, tmp_path):
    import os, time
    def fake_preview(voice, engine_id='', genre='', text=''):
        path = runner.preview_file(voice)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').write(b'ID3fake')
        return path, None
    monkeypatch.setattr(runner, 'preview', fake_preview)
    monkeypatch.setattr(runner.comfy, 'running', lambda: True)
    runner.preview_state.update(status='', voice='')
    assert client.get('/api/preview/audio?voice=Carl.mp3').status_code == 404
    r = client.post('/api/preview', json={'voice': 'Carl.mp3', 'genre': 'news'})
    assert r.status_code == 202
    for _ in range(50):
        if runner.preview_state['status'] != 'running':
            break
        time.sleep(0.05)
    st = client.get('/api/state').get_json()['preview']
    assert st['status'] == 'done' and st['voice'] == 'Carl.mp3' and st['genre'] == 'news'
    got = client.get('/api/preview/audio?voice=carl')          # matched like voices are: case and .mp3 ignored
    assert got.status_code == 200 and got.data == b'ID3fake'

def test_only_one_preview_at_a_time(client, monkeypatch):
    monkeypatch.setattr(runner.comfy, 'running', lambda: True)
    runner.preview_state.update(status='running', voice='Other.mp3')
    try:
        r = client.post('/api/preview', json={'voice': 'Carl.mp3'})
        assert r.status_code == 409 and 'Other.mp3' in r.get_json()['error']
    finally:
        runner.preview_state.update(status='', voice='')


def test_preview_uses_unsaved_settings_without_saving_them(client, monkeypatch):
    import json, time
    from a2p.engines import breeze
    seen = {}
    def fake_preview(voice, engine_id='', genre='', text=''):
        seen['guidance'] = breeze.preset(genre, voice, {})['narrator_guidance']      # what the engine would use
        seen['queue_view'] = None
        return None, 'stopped here'
    monkeypatch.setattr(runner, 'preview', fake_preview)
    monkeypatch.setattr(runner.comfy, 'running', lambda: True)
    runner.preview_state.update(status='', voice='')
    saved = json.load(open(settings.FILE))
    trial = dict(saved, genres={'news': {'narrator_guidance': 3.1}}, voices={'Carl.mp3': {'breeze': {'genres': {'news': {'narrator_guidance': 1.7}}}}})
    assert client.post('/api/preview', json={'voice': 'Carl.mp3', 'genre': 'news', 'settings': trial}).status_code == 202
    for _ in range(50):
        if runner.preview_state['status'] != 'running':
            break
        time.sleep(0.05)
    assert seen['guidance'] == 1.7 and runner.preview_state['unsaved'] is True
    assert json.load(open(settings.FILE)) == saved                       # nothing written
    assert settings.load().get('genres', {}).get('news') is None           # other threads still see the saved settings
    bad = dict(saved, voices={'Carl.mp3': {'vibevoice': {'speed': 5}}})
    r = client.post('/api/preview', json={'voice': 'Carl.mp3', 'settings': bad})
    assert r.status_code == 409 and 'speed' in r.get_json()['error']


def test_stats_hide_voices_without_a_sample(client, tmp_path):
    from a2p import stats
    (tmp_path / 'voices').mkdir()
    (tmp_path / 'voices' / 'Kept.mp3').write_bytes(b'')
    job = db.get_job(db.add_job(slug='k', title='T', author='A', site='S', text='x', voice_used='Kept.mp3', engine_used='breeze', status='done', info={}))
    stats.record_take(job, 'Kept.mp3', 'breeze', 'narr', 100, 1, 0.98, 0.98, 50)
    stats.record_take(job, 'Gone.mp3', 'breeze', 'narr', 100, 2, 0.80, 0.95, 50)
    d = client.get('/api/stats').get_json()
    assert [v['voice'] for v in d['voices']] == ['Kept']
    assert d['totals']['segments'] == 2        # totals still count every voice


def test_stats_voice_numbers_per_engine(client, tmp_path):
    from a2p import stats
    (tmp_path / 'voices').mkdir()
    (tmp_path / 'voices' / 'Kept.mp3').write_bytes(b'')
    job = db.get_job(db.add_job(slug='k', title='T', author='A', site='S', text='x', status='done', info={}))
    stats.record_take(job, 'Kept.mp3', 'breeze', 'narr', 100, 1, 0.98, 0.98, 50)
    stats.record_take(job, 'Kept.mp3', 'vibevoice', 'chunk', 100, 2, 0.80, 0.95, 50)
    v = client.get('/api/stats').get_json()['voices'][0]
    assert v['engines'] == ['breeze', 'vibevoice'] and v['segments'] == 2 and v['retry_rate'] == 0.5
    assert v['by_engine']['breeze']['segments'] == 1 and v['by_engine']['breeze']['retry_rate'] == 0
    assert v['by_engine']['vibevoice']['retry_rate'] == 1 and v['by_engine']['vibevoice']['engines'] == ['vibevoice']


def test_url_key_ignores_noise():
    assert fetch.url_key('https://www.Example.com/a/b/?utm_source=x&id=3#top') == fetch.url_key('http://example.com/a/b?id=3')
    assert fetch.url_key('https://example.com/a?id=3') != fetch.url_key('https://example.com/a?id=4')
    assert fetch.url_key('https://example.com/a?mode=x') != fetch.url_key('https://example.com/a')


def test_duplicate_url_in_queue_is_refused_before_fetching(client, monkeypatch):
    assert add(client, url='https://example.com/a').status_code == 200
    calls = []
    monkeypatch.setattr(fetch, 'from_url', lambda url: calls.append(url) or dict(ARTICLE))
    r = add(client, url='https://www.example.com/a/?utm_source=feed#x')
    assert r.status_code == 409 and r.get_json()['duplicate'] and not calls
    assert len(client.get('/api/state').get_json()['jobs']) == 1


def test_duplicate_paste_in_queue_is_refused(client, monkeypatch):
    item = dict(ARTICLE, source='paste')
    monkeypatch.setattr(fetch, 'from_paste', lambda text: dict(item))
    assert client.post('/api/add', json={'text': 'x'}).status_code == 200
    r = client.post('/api/add', json={'text': 'x'})
    assert r.status_code == 409 and r.get_json()['duplicate']


def test_already_made_asks_then_allows_again(client, tmp_path):
    job_id = add(client, url='https://example.com/a').get_json()['id']
    mp3 = tmp_path / 'made.mp3'
    mp3.write_bytes(b'')
    db.update_job(job_id, status='done', output_path=str(mp3))
    r = add(client, url='https://example.com/a')
    assert r.status_code == 409 and r.get_json()['already_made']
    assert client.post('/api/add', json={'url': 'https://example.com/a', 'again': True}).status_code == 200
    mp3.unlink()                                   # file gone from the library: no question
    db.update_job(job_id, status='done')
    jobs = [j for j in client.get('/api/state').get_json()['jobs'] if j['status'] != 'done']
    db.update_job(jobs[0]['id'], status='done')
    assert add(client, url='https://example.com/a').status_code == 200


def test_library_edits_keep_the_files_place(lib):
    import io, os
    from PIL import Image
    c = lib['client']
    os.utime(lib['old'], (1_000_000_000, 1_000_000_000))          # the old file: long ago
    os.utime(lib['mine'], (1_500_000_000, 1_500_000_000))         # mine: newer
    assert [f['title'] for f in c.get('/api/library').get_json()['files']] == ['A Headline', 'Made By The Old App']
    r = c.patch('/api/library', json={'path': 'Old Site/Ann Author/Old Site - Made By The Old App.mp3',
                                      'title': 'Renamed Old One', 'author': 'Ann Author', 'site': 'Old Site'})
    assert r.status_code == 200
    img = io.BytesIO(); Image.new('RGB', (20, 20), 'red').save(img, 'PNG')
    rel = r.get_json()['path']
    assert c.post('/api/library/art?path=' + rel, data={'image': (io.BytesIO(img.getvalue()), 'a.png')},
                  content_type='multipart/form-data').status_code == 200
    files = c.get('/api/library').get_json()['files']
    assert [f['title'] for f in files] == ['A Headline', 'Renamed Old One'] and files[1]['has_art']
    assert int(files[1]['modified']) == 1_000_000_000
