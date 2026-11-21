"""SQLite store: the queue, finished-job history, per-segment results,
track numbers and the per-job log. One file (data/a2p.db), one connection
per thread, every write committed immediately."""
import json, os, sqlite3, threading, time
from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'article',   -- article | youtube | audio | jwplayer | video
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | processing | done | failed | stopped
    url         TEXT, title TEXT, author TEXT, site TEXT,
    text        TEXT,               -- cleaned article body (title/byline are separate)
    art_path    TEXT,
    voice       TEXT,               -- the pick ("" = default); voice_used is what ran
    engine      TEXT,               -- the pick ("" = automatic)
    voice_used  TEXT, engine_used TEXT,
    error       TEXT,
    output_path TEXT,
    info        TEXT NOT NULL DEFAULT '{}',        -- JSON: genre, timings, source ids...
    added_at    REAL NOT NULL, started_at REAL, finished_at REAL
);
CREATE TABLE IF NOT EXISTS segments (
    job_id   INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    idx      INTEGER NOT NULL,
    plan     TEXT NOT NULL,         -- hash of everything that shapes the audio
    kind     TEXT, text TEXT, words INTEGER, gap REAL,
    path     TEXT,                  -- finished audio for this segment (NULL = not made yet)
    seed     INTEGER, match REAL, attempts INTEGER, seconds REAL,
    PRIMARY KEY (job_id, idx)
);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY, job_id INTEGER, ts REAL NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tracks (
    site TEXT NOT NULL, author TEXT NOT NULL, n INTEGER NOT NULL,
    PRIMARY KEY (site, author)
);
CREATE INDEX IF NOT EXISTS log_job ON log(job_id);
"""

_local = threading.local()
_path  = None
changes = 0          # bumped on every write; the web page pushes an update when it moves


def _touch():
    global changes
    changes += 1


def use(path):
    """Point the store at a different file (tests)."""
    global _path
    _path = path
    _local.__dict__.pop('con', None)


def con():
    c = getattr(_local, 'con', None)
    if c is None:
        path = _path or settings.DB_FILE
        if path != ':memory:':
            os.makedirs(os.path.dirname(path), exist_ok=True)
        c = sqlite3.connect(path, isolation_level=None, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA foreign_keys=ON')
        c.executescript(SCHEMA)
        _local.con = c
    return c


def _job(row):
    if row is None:
        return None
    d = dict(row)
    d['info'] = json.loads(d['info'] or '{}')
    return d


def add_job(**fields):
    fields.setdefault('added_at', time.time())
    if 'info' in fields:
        fields['info'] = json.dumps(fields['info'], ensure_ascii=False)
    cols = ', '.join(fields)
    cur = con().execute(f'INSERT INTO jobs ({cols}) VALUES ({", ".join("?" * len(fields))})',
                        list(fields.values()))
    _touch()
    return cur.lastrowid


def get_job(job_id):
    return _job(con().execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())


def jobs(status=None):
    """Oldest first. status may be a string or a tuple of strings."""
    if status is None:
        rows = con().execute('SELECT * FROM jobs ORDER BY added_at, id').fetchall()
    else:
        st = (status,) if isinstance(status, str) else tuple(status)
        rows = con().execute(f'SELECT * FROM jobs WHERE status IN ({", ".join("?" * len(st))}) '
                             'ORDER BY added_at, id', st).fetchall()
    return [_job(r) for r in rows]


def update_job(job_id, **fields):
    if 'info' in fields:
        fields['info'] = json.dumps(fields['info'], ensure_ascii=False)
    sets = ', '.join(f'{k}=?' for k in fields)
    con().execute(f'UPDATE jobs SET {sets} WHERE id=?', list(fields.values()) + [job_id])
    _touch()


def merge_info(job_id, **more):
    info = get_job(job_id)['info']
    info.update(more)
    update_job(job_id, info=info)


def delete_job(job_id):
    con().execute('DELETE FROM jobs WHERE id=?', (job_id,))
    con().execute('DELETE FROM log WHERE job_id=?', (job_id,))
    _touch()


def log(job_id, message):
    con().execute('INSERT INTO log (job_id, ts, message) VALUES (?,?,?)',
                  (job_id, time.time(), message))
    print(f'[job {job_id}] {message}' if job_id else message, flush=True)
    _touch()


def job_log(job_id):
    return [r['message'] for r in
            con().execute('SELECT message FROM log WHERE job_id=? ORDER BY id', (job_id,))]


# ---- segments -------------------------------------------------------------
def segments(job_id):
    return [dict(r) for r in
            con().execute('SELECT * FROM segments WHERE job_id=? ORDER BY idx', (job_id,))]


def set_plan(job_id, plan, segs):
    """Store the segment plan. Finished audio is kept only when the plan is
    unchanged (same text, voice, engine and settings); returns the rows."""
    old = segments(job_id)
    keep = {}
    if old and all(s['plan'] == plan for s in old) and len(old) == len(segs):
        keep = {s['idx']: s for s in old if s['path'] and os.path.isfile(s['path'])}
    else:
        for s in old:
            _remove(s['path'])
    con().execute('DELETE FROM segments WHERE job_id=?', (job_id,))
    for i, s in enumerate(segs):
        k = keep.get(i, {})
        con().execute(
            'INSERT INTO segments (job_id, idx, plan, kind, text, words, gap, path, seed, match, attempts, seconds) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (job_id, i, plan, s['kind'], s['text'], len(s['text'].split()), s.get('gap', 0.0),
             k.get('path'), k.get('seed'), k.get('match'), k.get('attempts'), k.get('seconds')))
    return segments(job_id)


def finish_segment(job_id, idx, path, seed, match, attempts, seconds):
    con().execute('UPDATE segments SET path=?, seed=?, match=?, attempts=?, seconds=? '
                  'WHERE job_id=? AND idx=?', (path, seed, match, attempts, seconds, job_id, idx))
    _touch()


def clear_segments(job_id):
    for s in segments(job_id):
        _remove(s['path'])
    con().execute('DELETE FROM segments WHERE job_id=?', (job_id,))


def _remove(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


# ---- track numbers --------------------------------------------------------
def next_track(site, author):
    c = con()
    c.execute('INSERT INTO tracks (site, author, n) VALUES (?,?,1) '
              'ON CONFLICT(site, author) DO UPDATE SET n = n + 1', (site, author))
    return c.execute('SELECT n FROM tracks WHERE site=? AND author=?', (site, author)).fetchone()['n']


def import_tracks(mapping):
    """{'Site|Author': n} from the old app's track-log.json; never lowers a count."""
    for key, n in mapping.items():
        site, _, author = key.partition('|')
        con().execute('INSERT INTO tracks (site, author, n) VALUES (?,?,?) '
                      'ON CONFLICT(site, author) DO UPDATE SET n = max(n, excluded.n)',
                      (site, author, int(n)))
