"""Statistics: which voices need retries most, how fast each engine is,
what failed. Kept in two tables that outlive the jobs (deleting a file from
the library does not delete its history):

  runs   one row per article or download that finished or failed
  takes  one row per generated segment: how many takes it needed, the
         Whisper match of the first and the kept take, and the time spent

History from the original app's CSV logs can be imported once
(import_old_app), so the numbers do not start from zero.
"""
import csv, json, os, time, datetime
from . import db, settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, job_id INTEGER, source TEXT NOT NULL DEFAULT 'new',
    title TEXT, site TEXT, kind TEXT, voice TEXT, engine TEXT, genre TEXT, status TEXT,
    words INTEGER, segments INTEGER, retried INTEGER, match_mean REAL, match_min REAL,
    generate_seconds REAL, total_seconds REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS takes (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, job_id INTEGER, source TEXT NOT NULL DEFAULT 'new',
    voice TEXT, engine TEXT, kind TEXT, words INTEGER, attempts INTEGER,
    first_match REAL, match REAL, seconds REAL
);
CREATE INDEX IF NOT EXISTS runs_ts ON runs(ts);
CREATE INDEX IF NOT EXISTS takes_ts ON takes(ts);
"""


def _con():
    c = db.con()
    if not getattr(c, '_stats_ready', False):
        c.executescript(SCHEMA)
        try:
            c._stats_ready = True
        except AttributeError:
            pass
    return c


# ---- recording ------------------------------------------------------------
def record_take(job, voice, engine, kind, words, attempts, first_match, match, seconds):
    _con().execute('INSERT INTO takes (ts, job_id, voice, engine, kind, words, attempts, first_match, match, seconds) '
                   'VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (time.time(), job['id'], voice, engine, kind, words, attempts, first_match, match, seconds))


def record_run(job, status, error=None):
    """Called once per finished or failed job (not for stops)."""
    job  = db.get_job(job['id']) or job
    info = job.get('info') or {}
    _con().execute(
        'INSERT INTO runs (ts, job_id, title, site, kind, voice, engine, genre, status, words, segments, retried, '
        'match_mean, match_min, generate_seconds, total_seconds, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (time.time(), job['id'], job.get('title'), job.get('site'), job.get('kind'), job.get('voice_used'),
         job.get('engine_used'), info.get('genre'), status, info.get('words'), info.get('segments'),
         info.get('retried_segments'), info.get('match_mean'), info.get('match_min'),
         info.get('generate_seconds'), info.get('total_seconds'), error))


# ---- the numbers ------------------------------------------------------------
def _name(voice):
    return os.path.splitext(os.path.basename(voice or ''))[0] or '(unknown)'


def summary(days=None):
    """Everything the Stats tab shows. days=None means all time."""
    since = time.time() - days * 86400 if days else 0
    c = _con()
    runs  = [dict(r) for r in c.execute('SELECT * FROM runs WHERE ts >= ? ORDER BY ts', (since,))]
    takes = [dict(r) for r in c.execute('SELECT * FROM takes WHERE ts >= ?', (since,))]
    articles = [r for r in runs if (r['kind'] or 'article') == 'article']

    def group(rows, key):
        out = {}
        for r in rows:
            out.setdefault(key(r), []).append(r)
        return out

    def avg(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 4) if xs else None

    def take_numbers(ts):
        n = len(ts)
        retried = sum(1 for t in ts if (t['attempts'] or 1) > 1)
        words = sum(t['words'] or 0 for t in ts)
        secs = sum(t['seconds'] or 0 for t in ts)
        return {'segments': n, 'retried': retried, 'retry_rate': round(retried / n, 4) if n else None,
                'first_match': avg(t['first_match'] for t in ts), 'match': avg(t['match'] for t in ts),
                'worst_match': min((t['match'] for t in ts if t['match'] is not None), default=None),
                'seconds_per_word': round(secs / words, 2) if words and secs else None}

    voices = []
    t_by_voice = group(takes, lambda t: _name(t['voice']))
    r_by_voice = group(articles, lambda r: _name(r['voice']))
    for name in sorted(set(t_by_voice) | set(r_by_voice)):
        rs = r_by_voice.get(name, [])
        row = {'voice': name, 'articles': sum(1 for r in rs if r['status'] == 'done'),
               'failed': sum(1 for r in rs if r['status'] == 'failed'),
               'engines': sorted({t['engine'] for t in t_by_voice.get(name, []) if t['engine']} |
                                 {r['engine'] for r in rs if r['engine']})}
        row.update(take_numbers(t_by_voice.get(name, [])))
        voices.append(row)
    # most trouble first: share of segments needing a retry, then failed articles
    voices.sort(key=lambda v: (-(v['retry_rate'] or 0), -v['failed'], v['voice']))

    engines = []
    for eng, ts in sorted(group(takes, lambda t: t['engine'] or '(unknown)').items()):
        rs = [r for r in articles if (r['engine'] or '(unknown)') == eng]
        row = {'engine': eng, 'articles': sum(1 for r in rs if r['status'] == 'done'),
               'failed': sum(1 for r in rs if r['status'] == 'failed')}
        row.update(take_numbers(ts))
        engines.append(row)

    genres = []
    for g, rs in sorted(group([r for r in articles if r['genre'] and r['status'] == 'done'], lambda r: r['genre']).items()):
        genres.append({'genre': g, 'articles': len(rs), 'match': avg(r['match_mean'] for r in rs)})

    done = [r for r in runs if r['status'] == 'done']
    failures = [{'ts': r['ts'], 'title': r['title'], 'voice': _name(r['voice']) if r['voice'] else '',
                 'engine': r['engine'], 'error': (r['error'] or '')[:300], 'source': r['source']}
                for r in reversed(runs) if r['status'] == 'failed'][:15]
    first = min([r['ts'] for r in runs] + [t['ts'] for t in takes], default=None)
    return {
        'days': days, 'since': first,
        'totals': {'articles': sum(1 for r in done if (r['kind'] or 'article') == 'article'),
                   'downloads': sum(1 for r in done if (r['kind'] or 'article') != 'article'),
                   'failed': sum(1 for r in runs if r['status'] == 'failed'),
                   'words': sum(r['words'] or 0 for r in done),
                   'generate_hours': round(sum(r['generate_seconds'] or r['total_seconds'] or 0 for r in done) / 3600, 1),
                   'segments': len(takes),
                   'retry_rate': take_numbers(takes)['retry_rate'],
                   'match': avg(t['match'] for t in takes)},
        'voices': voices, 'engines': engines, 'genres': genres, 'failures': failures,
        'imported': bool(c.execute("SELECT 1 FROM runs WHERE source='old app' LIMIT 1").fetchone()),
    }


# ---- importing the original app's logs ----------------------------------------
def _ts(text):
    try:
        return time.mktime(datetime.datetime.strptime(text, '%Y-%m-%d %H:%M:%S').timetuple())
    except Exception:
        return None


def _num(x):
    try:
        return float(x) if x not in (None, '') else None
    except ValueError:
        return None


def import_old_app(log_dir=None):
    """Read the original app's CSV/JSONL logs into runs and takes (once;
    re-running replaces the earlier import). Returns counts."""
    log_dir = log_dir or os.path.join(settings.ROOT, 'log')
    if not os.path.isfile(os.path.join(log_dir, 'generation-log.csv')):
        # The original logs are gone (deleted after the cutover import): keep what was imported.
        return {'runs': 0, 'takes': 0, 'new_app_jobs': backfill_new_jobs(), 'note': 'no original-app logs found'}
    c = _con()
    c.execute("DELETE FROM runs WHERE source='old app'")
    c.execute("DELETE FROM takes WHERE source='old app'")
    read = lambda name: list(csv.DictReader(open(os.path.join(log_dir, name), encoding='utf-8'))) \
        if os.path.isfile(os.path.join(log_dir, name)) else []

    gen      = read('generation-log.csv')
    quality  = read('ai-quality-articles.csv')
    breeze   = read('breeze-articles.csv')
    chunks   = read('ai-quality-chunks.csv')
    details  = []
    p = os.path.join(log_dir, 'breeze-details.jsonl')
    if os.path.isfile(p):
        details = [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]
    breeze_slugs = {r['slug'] for r in breeze}

    def nearest(rows, slug, ts, window=6 * 3600):
        best = None
        for r in rows:
            t = _ts(r['timestamp'])
            if r['slug'] == slug and t is not None and abs(t - ts) <= window:
                if best is None or abs(t - ts) < abs(_ts(best['timestamp']) - ts):
                    best = r
        return best

    n_runs = 0
    for r in gen:
        slug, status = r['slug'], r['status']
        if slug.endswith('-art') or (slug[-4:-2] == '-c' and slug[-2:].isdigit()):
            continue                                        # art and per-chunk rows
        if status not in ('success', 'error', 'timeout'):
            continue                                        # "normalized", "cancelled" ...
        ts = _ts(r['timestamp'])
        if ts is None:
            continue
        is_breeze = r['engine'] == 'breeze' or 'segment(s), genre' in (r['detail'] or '') or slug in breeze_slugs
        engine = r['engine'] or ('breeze' if is_breeze else 'vibevoice')
        q = nearest(breeze if engine == 'breeze' else quality, slug, ts)
        if engine == 'breeze' and q:
            mean, worst, segs, retried, gen_s, genre = q['match_mean'], q['match_min'], q['segments'], None, q['gen_seconds'], q['genre']
        elif q:
            mean, worst, segs, retried, gen_s, genre = (q['match_final_mean'], q['match_final_min'], q['chunks'],
                                                        q['chunks_retried'], q['gen_seconds_total'], None)
        else:
            mean = worst = segs = retried = gen_s = genre = None
        c.execute('INSERT INTO runs (ts, source, title, kind, voice, engine, genre, status, words, segments, retried, '
                  'match_mean, match_min, generate_seconds, total_seconds, error) '
                  "VALUES (?, 'old app', ?, 'article', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (ts, slug, r['voice'], engine, genre, 'done' if status == 'success' else 'failed',
                   int(_num(r['word_count']) or 0), int(_num(segs) or 0) or None, int(_num(retried) or 0) if retried not in (None, '') else None,
                   _num(mean), _num(worst), _num(gen_s), _num(r['duration_seconds']),
                   None if status == 'success' else (r['detail'] or status)))
        n_runs += 1

    # VibeVoice chunks: one row per attempt; group the attempts of each chunk
    n_takes = 0
    groups = []
    for r in chunks:
        if r.get('error'):
            continue
        key = (r['slug'], r['chunk'])
        if groups and groups[-1]['key'] == key and int(r['attempt'] or 1) > 1:
            groups[-1]['rows'].append(r)
        else:
            groups.append({'key': key, 'rows': [r]})
    for g in groups:
        rows = g['rows']
        kept = next((x for x in rows if x['kept'] == 'True'), rows[-1])
        ts = _ts(rows[0]['timestamp'])
        if ts is None:
            continue
        c.execute("INSERT INTO takes (ts, source, voice, engine, kind, words, attempts, first_match, match, seconds) "
                  "VALUES (?, 'old app', ?, ?, 'chunk', ?, ?, ?, ?, ?)",
                  (ts, rows[0]['voice'], 'breeze' if g['key'][0] in breeze_slugs else 'vibevoice',
                   int(_num(rows[0]['words']) or 0), len(rows), _num(rows[0]['match']), _num(kept['match']),
                   sum(_num(x['gen_seconds']) or 0 for x in rows)))
        n_takes += 1

    # Breeze segments (attempt counts and kept match; the first take's match was not logged)
    voice_of = {r['slug']: r['voice'] for r in breeze}
    for d in details:
        ts = _ts(d.get('timestamp', ''))
        for s in d.get('segments') or []:
            c.execute("INSERT INTO takes (ts, source, voice, engine, kind, words, attempts, first_match, match, seconds) "
                      "VALUES (?, 'old app', ?, 'breeze', ?, ?, ?, ?, ?, ?)",
                      (ts or 0, voice_of.get(d['slug']), s.get('kind'), s.get('words'), s.get('attempts') or 1,
                       s.get('match') if (s.get('attempts') or 1) == 1 else None, s.get('match'), s.get('seconds')))
            n_takes += 1
    db._touch()
    return {'runs': n_runs, 'takes': n_takes, 'new_app_jobs': backfill_new_jobs()}


def backfill_new_jobs():
    """Runs for jobs this app finished before statistics were recorded
    (their per-segment detail is gone, so they add no takes)."""
    c = _con()
    have = {r['job_id'] for r in c.execute("SELECT job_id FROM runs WHERE source='new'")}
    n = 0
    for job in db.jobs(('done', 'failed')):
        if job['id'] not in have and not job['info'].get('imported'):   # imported jobs are already in the history
            record_run(job, job['status'], job['error'])
            c.execute('UPDATE runs SET ts=? WHERE id=(SELECT max(id) FROM runs)',
                      (job['finished_at'] or job['started_at'] or job['added_at'],))
            n += 1
    return n
