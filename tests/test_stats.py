"""Statistics: recording, the summary, and importing the original app's logs."""
import csv, json
from a2p import db, stats
from test_core import conf     # noqa: F401  (fixture)


def _job(voice, engine='breeze', status='done', **info):
    job_id = db.add_job(slug='a', title='T', author='A', site='S', text='x', voice_used=voice, engine_used=engine,
                        status=status, info=dict({'words': 500, 'segments': 3, 'match_mean': 0.97}, **info))
    return db.get_job(job_id)


def test_summary_ranks_voices_by_retries(conf):
    conf()
    a, b = _job('Steady.mp3'), _job('Shaky.mp3')
    for _ in range(4):
        stats.record_take(a, 'Steady.mp3', 'breeze', 'narr', 100, 1, 0.98, 0.98, 50)
    stats.record_take(b, 'Shaky.mp3', 'vibevoice', 'chunk', 200, 2, 0.80, 0.95, 300)
    stats.record_take(b, 'Shaky.mp3', 'vibevoice', 'chunk', 200, 1, 0.96, 0.96, 200)
    stats.record_run(a, 'done')
    stats.record_run(b, 'done')
    stats.record_run(_job('Shaky.mp3', engine='vibevoice', status='failed'), 'failed', 'Timed out')
    s = stats.summary()
    assert [v['voice'] for v in s['voices']] == ['Shaky', 'Steady']
    shaky = s['voices'][0]
    assert (shaky['segments'], shaky['retried'], shaky['retry_rate'], shaky['failed']) == (2, 1, 0.5, 1)
    assert shaky['first_match'] == 0.88 and shaky['match'] == 0.955 and shaky['seconds_per_word'] == 1.25
    assert s['totals']['articles'] == 2 and s['totals']['failed'] == 1 and s['totals']['retry_rate'] == round(1 / 6, 4)
    assert {e['engine'] for e in s['engines']} == {'breeze', 'vibevoice'}
    assert s['failures'][0]['error'] == 'Timed out' and s['failures'][0]['voice'] == 'Shaky'


def test_period_filter(conf):
    conf()
    a = _job('Old.mp3')
    stats.record_take(a, 'Old.mp3', 'breeze', 'narr', 100, 1, 0.9, 0.9, 10)
    db.con().execute('UPDATE takes SET ts = ts - 40 * 86400')
    assert stats.summary(days=30)['totals']['segments'] == 0 and stats.summary()['totals']['segments'] == 1


def _write_csv(path, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


GEN = ['timestamp', 'slug', 'word_count', 'voice', 'status', 'duration_seconds', 'detail', 'engine']
CHUNK = ['timestamp', 'slug', 'voice', 'chunk', 'words', 'mode', 'attempt', 'seed', 'gen_seconds',
         'asr_seconds', 'match', 'min_match', 'kept', 'error']


def test_import_old_app_logs(conf, tmp_path):
    conf()
    _write_csv(tmp_path / 'generation-log.csv', [dict(zip(GEN, r)) for r in [
        ('2026-11-01 10:00:00', 'story-one', '900', 'A.mp3', 'success', '600', '2 chunk(s)', ''),
        ('2026-11-01 10:00:01', 'story-one', '900', 'A.mp3', 'normalized', '3', '', ''),
        ('2026-11-01 09:50:00', 'story-one-c01', '450', 'A.mp3', 'success', '300', '', ''),
        ('2026-11-02 10:00:00', 'story-two', '700', 'B.mp3', 'timeout', '3600', 'Timed out after 3600s', ''),
        ('2026-11-14 10:00:00', 'story-three', '300', 'B.mp3', 'success', '200', '5 segment(s), genre news', ''),
        ('2026-11-03 10:00:00', 'x-art', '0', '-', 'success', '20', '', '')]])
    _write_csv(tmp_path / 'ai-quality-chunks.csv', [dict(zip(CHUNK, r)) for r in [
        ('2026-11-01 09:50:00', 'story-one', 'A.mp3', '1', '450', 'retry', '1', '', '300', '5', '0.85', '0.9', 'False', ''),
        ('2026-11-01 09:55:00', 'story-one', 'A.mp3', '1', '450', 'retry', '2', '7', '310', '5', '0.95', '0.9', 'True', ''),
        ('2026-11-01 10:00:00', 'story-one', 'A.mp3', '2', '450', 'retry', '1', '', '290', '5', '0.97', '0.9', 'True', '')]])
    _write_csv(tmp_path / 'breeze-articles.csv', [
        {'timestamp': '2026-11-14 10:00:00', 'slug': 'story-three', 'genre': 'news', 'segments': '5', 'words': '300',
         'gen_seconds': '150', 'match_mean': '0.97', 'match_min': '0.9', 'voice': 'B.mp3'}])
    with open(tmp_path / 'breeze-details.jsonl', 'w', encoding='utf-8') as f:
        f.write(json.dumps({'timestamp': '2026-11-14 10:00:00', 'slug': 'story-three', 'segments': [
            {'kind': 'title', 'words': 10, 'attempts': 1, 'match': 1.0, 'seconds': 5},
            {'kind': 'narr', 'words': 100, 'attempts': 2, 'match': 0.95, 'seconds': 80}]}) + '\n')

    got = stats.import_old_app(str(tmp_path))
    assert got['runs'] == 3 and got['takes'] == 4
    assert stats.import_old_app(str(tmp_path))['runs'] == 3          # re-running replaces, never doubles
    s = stats.summary()
    a = next(v for v in s['voices'] if v['voice'] == 'A')
    assert (a['articles'], a['segments'], a['retried'], a['first_match'], a['match']) == (1, 2, 1, 0.91, 0.96)
    b = next(v for v in s['voices'] if v['voice'] == 'B')
    assert (b['articles'], b['failed'], b['engines']) == (1, 1, ['breeze', 'vibevoice'])
    assert s['imported'] and s['genres'] == [{'genre': 'news', 'articles': 1, 'match': 0.97}]


def test_stats_api(conf):
    conf()
    from a2p import web
    web.app.config['TESTING'] = True
    c = web.app.test_client()
    assert c.get('/api/stats').get_json()['totals']['articles'] == 0
    assert c.get('/api/stats?days=30').status_code == 200
