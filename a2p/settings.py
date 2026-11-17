"""Settings: one JSON file (settings.json), merged over the defaults here.

Everything is optional. The file is read fresh each time it changes, so
edits apply to the next job without a restart. A syntax error is reported
with its line and column instead of silently using defaults.
"""
import contextlib, copy, json, os, threading

ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the Article2Pod folder
FILE      = os.path.join(ROOT, 'settings.json')
DATA_DIR  = os.path.join(ROOT, 'data')
WORK_DIR  = os.path.join(DATA_DIR, 'work')
DB_FILE   = os.path.join(DATA_DIR, 'a2p.db')
WORKFLOWS = os.path.join(ROOT, 'workflow')

ANCHOR = 'Confident, energetic news anchor delivery: loud, clear, brisk and authoritative.'

DEFAULTS = {
    'port': 8080,
    'comfy': {
        'url': 'http://127.0.0.1:8000',
        'data_dir': None,            # None = ask ComfyUI Desktop (its basePath)
        'install_dir': None,         # None = find ComfyUI Desktop's copy; else the folder holding ComfyUI\\main.py
        'start_when_needed': True,   # launch ComfyUI for the queue when it isn't running
        'startup_timeout': 180,
        'listen': '0.0.0.0',         # so other devices on the network can reach a ComfyUI we start
        'extra_args': [],            # extra ComfyUI flags, e.g. "--use-sage-attention"
    },
    'voices_dir': os.path.join(ROOT, 'voices'),
    'output_dir': os.path.join(ROOT, 'output'),
    'default_voice': 'shuffle',      # a file name in voices_dir, or "shuffle"
    'default_engine': 'breeze',      # used when both engines are installed and nothing else picks
    'group_by_engine': False,
    'normalize': True,               # loudness-normalize the finished MP3
    'quality_check': True,           # Whisper check of every segment, retry bad ones
    'quality': {'min_match': 0.92, 'max_attempts': 3, 'device': 'cuda'},
    'whisper_model_dir': None,       # needed for quality_check and Breeze voice transcripts
    'text_model': True,              # genre + quote detection for engines that use them
    'text_model_timeout': 1800,
    'cleanup': True,                 # text model removes leftover page junk and fixes title/author/site
    'cleanup_options': {'max_remove_fraction': 0.30, 'fix_metadata': True, 'preview_words': 22},
    'generate_art': True,            # make cover art when the article has none (needs workflows/art.json)
    'art_prompt': True,              # text model writes the image prompt (else a title template)
    'art': {'timeout': 600, 'excerpt_words': 700},
    'engines': {
        'breeze': {
            'guidance': 1.5, 'quote_guidance': 2.5, 'temperature': 0.9, 'seed': 42,
            'narrator': 'Clear, natural, conversational delivery.',
            'quote': 'Clear, natural, conversational delivery.',
            'max_segment_words': 170, 'max_reference_seconds': 60,
            'sentence_gap': 0.45, 'switch_gap': 0.65, 'title_gap': 0.9, 'end_pause': 3.0,
            'timeout': 1800,
        },
        'vibevoice': {'chunk_words': 1400, 'end_pause': 3.0, 'timeout': 3600},
    },
    'default_genre': '',
    'genres': {},        # name -> {description, narrator, narrator_guidance, quote, quote_guidance, quote_cue, quote_end_pause, temperature}
    'voices': {},        # voice name -> {engine, breeze: {guidance, ..., genres: {name: {...}}}}
    'sites': {},         # domain -> {voice, engine, genre, art, paste_only, breeze: {...}}
    'pronunciations': {},
    'strip_lines': [],
    'keep_lines': [],    # clean-up never removes a line containing one of these (or matching "re:<pattern>")
    'ad_markers': [],
    'strip_pull_quotes': True,
}

_cache = {'mtime': None, 'value': None}
_trial = threading.local()      # unsaved settings in force for this thread only (voice previews)


class SettingsError(Exception):
    pass


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict) and k not in ('genres', 'voices', 'sites', 'pronunciations'):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(force=False):
    """The merged settings dict. Raises SettingsError on a broken file.
    Inside trial() it returns the trial settings instead, for that thread only."""
    if getattr(_trial, 'value', None) is not None:
        return _trial.value
    mtime = os.path.getmtime(FILE) if os.path.isfile(FILE) else 0
    if not force and _cache['mtime'] == mtime and _cache['value'] is not None:
        return _cache['value']
    over = {}
    if mtime:
        with open(FILE, 'r', encoding='utf-8') as f:
            text = f.read()
        try:
            over = json.loads(text)
        except json.JSONDecodeError as e:
            raise SettingsError(f'settings.json line {e.lineno}, column {e.colno}: {e.msg}')
        if not isinstance(over, dict):
            raise SettingsError('settings.json must be a JSON object')
    value = _merge(copy.deepcopy(DEFAULTS), over)
    for problem in validate(value):
        raise SettingsError(problem)
    _cache.update(mtime=mtime, value=value)
    return value


def validate(s):
    """Yields human-readable problems (unknown keys are the usual typo)."""
    for k in s:
        if k not in DEFAULTS:
            yield f'settings.json: unknown setting "{k}"'
    for name, g in s['genres'].items():
        for k in g:
            if k not in GENRE_KEYS:
                yield f'settings.json: genre "{name}" has unknown key "{k}"'
    for name, v in s['voices'].items():
        for k in v:
            if k not in ('engine', 'breeze', 'vibevoice'):
                yield f'settings.json: voice "{name}" has unknown key "{k}"'
        for k in (v.get('breeze') or {}):
            if k not in VOICE_BREEZE_KEYS:
                yield f'settings.json: voice "{name}" breeze setting "{k}" is not known'
        for k, val in (v.get('vibevoice') or {}).items():
            if k not in VOICE_VIBEVOICE_KEYS:
                yield f'settings.json: voice "{name}" vibevoice setting "{k}" is not known'
            else:
                lo, hi = VOICE_VIBEVOICE_KEYS[k]
                if not isinstance(val, (int, float)) or not lo <= val <= hi:
                    yield f'settings.json: voice "{name}" vibevoice {k} must be a number from {lo} to {hi}'
    for name, v in s['sites'].items():
        for k in v:
            if k not in ('voice', 'engine', 'genre', 'art', 'paste_only', 'breeze', 'cleanup', 'keep_lines'):
                yield f'settings.json: site "{name}" has unknown key "{k}"'
    if s['default_genre'] and s['default_genre'] not in s['genres']:
        yield f'settings.json: default_genre "{s["default_genre"]}" is not one of the genres'


PRESET_KEYS       = ('narrator', 'narrator_guidance', 'quote', 'quote_guidance',
                     'quote_cue', 'quote_end_pause', 'temperature')
GENRE_KEYS        = PRESET_KEYS + ('description',)
VOICE_BREEZE_KEYS = PRESET_KEYS + ('guidance', 'genres')
# VibeVoice per-voice settings and the range its ComfyUI node accepts
VOICE_VIBEVOICE_KEYS = {'speed': (0.8, 1.2), 'guidance': (0.5, 3.5)}


def check(overrides):
    """The merged settings for a set of overrides, without saving them.
    Raises SettingsError when they are not valid."""
    if not isinstance(overrides, dict):
        raise SettingsError('Settings must be a JSON object')
    value = _merge(copy.deepcopy(DEFAULTS), copy.deepcopy(overrides))
    for problem in validate(value):
        raise SettingsError(problem.replace('settings.json: ', ''))
    return value


@contextlib.contextmanager
def trial(overrides):
    """Use unsaved overrides for everything this thread does inside the
    block (other threads, such as the queue, keep the saved settings)."""
    _trial.value = check(overrides)
    try:
        yield _trial.value
    finally:
        _trial.value = None


def save(overrides):
    """Write the user's overrides (not the merged defaults) back to disk."""
    with open(FILE, 'w', encoding='utf-8') as f:
        json.dump(overrides, f, indent=2, ensure_ascii=False)
        f.write('\n')
    _cache['mtime'] = None


def comfy_data_dir():
    """ComfyUI Desktop's data folder (input, output, models, .venv)."""
    explicit = load()['comfy']['data_dir']
    if explicit:
        return explicit
    try:
        with open(os.path.join(os.environ.get('APPDATA', ''), 'ComfyUI', 'config.json'),
                  encoding='utf-8') as f:
            base = json.load(f).get('basePath')
        if base and os.path.isdir(base):
            return base
    except Exception:
        pass
    return os.path.dirname(ROOT)


def comfy_input_dir():
    return os.path.join(comfy_data_dir(), 'input')

def comfy_output_dir():
    return os.path.join(comfy_data_dir(), 'output')

def comfy_python():
    return os.path.join(comfy_data_dir(), '.venv', 'Scripts', 'python.exe')


def name_key(voice_or_file):
    """'BillBurr.mp3' / 'billburr' -> 'billburr' (how voices are matched)."""
    return os.path.splitext(os.path.basename(voice_or_file or ''))[0].lower()


def voice_entry(voice):
    want = name_key(voice)
    for name, entry in load()['voices'].items():
        if name_key(name) == want and isinstance(entry, dict):
            return entry
    return {}


def site_entry(url='', site_name=''):
    """The "sites" entry for a URL's domain (suffix match) or, for pasted
    text, a site name ('Esquire' matches 'esquire.com')."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower().replace('www.', '') if url else ''
    hint   = (site_name or '').strip().lower().replace('www.', '')
    for name, entry in load()['sites'].items():
        d = name.lower().replace('www.', '').strip()
        bare = d.rsplit('.', 1)[0]
        if (domain and (domain == d or domain.endswith('.' + d))) or (hint and hint in (d, bare)):
            return entry
    return {}
