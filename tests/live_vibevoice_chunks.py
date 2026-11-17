"""Live comparison (needs ComfyUI): read the same article with VibeVoice at
two chunk sizes and report timing and Whisper scores for each.

    python tests/live_vibevoice_chunks.py <url> <voice.mp3> 450 250

Each run is filed in the output folder with a "VV <size> - " label. The
chunk_words setting is put back afterwards.
"""
import json, os, sys, time
sys.path.insert(0, '.')
from a2p import db, fetch, runner, settings

url, voice, sizes = sys.argv[1], sys.argv[2], [int(x) for x in sys.argv[3:]]


def overrides():
    with open(settings.FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def set_chunk(words):
    ov = overrides()
    ov.setdefault('engines', {}).setdefault('vibevoice', {})['chunk_words'] = words
    settings.save(ov)
    settings.load(force=True)


original = settings.load()['engines']['vibevoice']['chunk_words']
results = []
try:
    for size in sizes:
        set_chunk(size)
        job_id = fetch.add(url=url)
        job = db.get_job(job_id)
        info = dict(job['info'], metadata_edited=True)        # keep the label through clean-up
        db.update_job(job_id, title=f'VV {size} - {job["title"]}', voice=voice, engine='vibevoice', info=info)
        t = time.time()
        runner.run_queue()
        job = db.get_job(job_id)
        if job['status'] != 'done':
            print(f'>>> {size}: {job["status"]} {job["error"]}', flush=True)
            results.append((size, job['status'], None))
            continue
        d, n = os.path.split(job['output_path'])
        new = os.path.join(d, f'VV {size} - ' + n.replace(f' - VV {size} - ', ' - ', 1))
        os.rename(job['output_path'], new)
        db.update_job(job_id, output_path=new)
        i = job['info']
        results.append((size, 'done', {'chunks': i['segments'], 'generate_seconds': i['generate_seconds'],
                                       'wall_seconds': round(time.time() - t), 'match_mean': i['match_mean'],
                                       'match_min': i['match_min'], 'retried': i['retried_segments'],
                                       'words': i['words']}))
        print(f'>>> {size}: {results[-1][2]}', flush=True)
        print('    ' + '\n    '.join(m for m in db.job_log(job_id) if m.startswith('Segment')), flush=True)
finally:
    set_chunk(original)
print('>>> RESULTS', json.dumps(results), flush=True)
