"""Starting and stopping ComfyUI.

Normally ComfyUI Desktop is already open and this module does nothing.
When it isn't, the app can launch ComfyUI itself, with the same command
line ComfyUI Desktop uses, so the same models, custom nodes and user data
are seen. The app only ever stops a ComfyUI it started.
"""
import glob, os, subprocess, threading, time
from . import comfy, db, settings

_proc = None          # the ComfyUI process we started, if any
_log  = None
_lock = threading.Lock()
busy  = ''            # '', 'starting' or 'stopping'
_popen = subprocess.Popen     # the one place a ComfyUI process is created (tests replace it)

# ComfyUI Desktop keeps its copy of ComfyUI in one of these, under %LOCALAPPDATA%.
INSTALLS = (os.path.join('Comfy-Desktop', 'ComfyUI-Installs', 'ComfyUI'),
            os.path.join('Programs', '@comfyorgcomfyui-electron', 'resources'))


def install_dir():
    """The folder holding ComfyUI\\main.py, or None when it can't be found."""
    explicit = settings.load()['comfy'].get('install_dir')
    candidates = [explicit] if explicit else [os.path.join(os.environ.get('LOCALAPPDATA', ''), rel)
                                              for rel in INSTALLS]
    for folder in candidates:
        if folder and os.path.isfile(os.path.join(folder, 'ComfyUI', 'main.py')):
            return folder
    return None


def model_paths_file():
    """The extra-model-paths file to launch with: ComfyUI Desktop's own
    (newest) if it has written one, else one built from the shared model
    folders that exist."""
    theirs = sorted(glob.glob(os.path.join(os.environ.get('APPDATA', ''), 'Comfy Desktop',
                                           'instance-model-paths', '*.yaml')), key=os.path.getmtime)
    if theirs:
        return theirs[-1]
    sections = []
    shared = [os.path.join(settings.comfy_data_dir(), 'models'),
              os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Comfy-Desktop', 'ComfyUI-Shared', 'models')]
    for i, folder in enumerate(shared):
        subs = sorted(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))) \
            if os.path.isdir(folder) else []
        if subs:
            # single-quoted YAML keeps Windows backslashes literal
            sections.append('\n'.join([f'a2p_shared_{i}:', f"    base_path: '{folder}'"] +
                                      [f'    {name}: {name}' for name in subs]))
    if not sections:
        return None
    path = os.path.join(settings.DATA_DIR, 'extra_model_paths.yaml')
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(sections) + '\n')
    return path


def command():
    """(argument list, working folder) to launch ComfyUI, or raises RuntimeError."""
    cfg     = settings.load()['comfy']
    install = install_dir()
    if not install:
        raise RuntimeError('ComfyUI was not found. Open ComfyUI Desktop, or set comfy.install_dir '
                           'in the settings to the folder that contains ComfyUI\\main.py.')
    python = settings.comfy_python()
    if not os.path.isfile(python):
        raise RuntimeError(f'ComfyUI\'s Python was not found at {python}.')
    data = settings.comfy_data_dir()
    port = cfg['url'].rstrip('/').rsplit(':', 1)[-1]
    args = [python, '-s', os.path.join('ComfyUI', 'main.py'), '--enable-assets',
            '--base-directory', data,
            '--user-directory', os.path.join(data, 'user'),
            '--database-url', 'sqlite:///' + os.path.join(data, 'user', 'comfyui.db'),
            '--port', port, '--preview-method', 'auto', '--enable-manager',
            '--listen', cfg['listen'],
            '--input-directory', settings.comfy_input_dir(),
            '--output-directory', settings.comfy_output_dir()]
    args += [str(a) for a in cfg['extra_args']]
    paths = model_paths_file()
    if paths:
        args += ['--extra-model-paths-config', paths]
    return args, install


def ours():
    return _proc is not None and _proc.poll() is None


def status():
    return {'running': comfy.running(), 'ours': ours(), 'busy': busy}


def start(wait=True):
    """Launch ComfyUI and wait until it answers. Returns (ok, error)."""
    global _proc, _log, busy
    with _lock:
        if busy:
            return False, f'ComfyUI is already {busy}.'
        if comfy.running():
            return True, None
        busy = 'starting'
    try:
        args, cwd = command()
        log_path = os.path.join(settings.DATA_DIR, 'comfyui-console.log')
        os.makedirs(settings.DATA_DIR, exist_ok=True)
        _log  = open(log_path, 'w', encoding='utf-8', errors='replace')
        _proc = _popen(args, cwd=cwd, stdout=_log, stderr=subprocess.STDOUT)
        db.log(None, f'Starting ComfyUI (its output goes to {log_path})...')
        deadline = time.time() + int(settings.load()['comfy']['startup_timeout'])
        while wait and time.time() < deadline:
            if comfy.running():
                db.log(None, 'ComfyUI is ready.')
                return True, None
            if _proc.poll() is not None:
                return False, f'ComfyUI exited while starting (see {log_path}).'
            if comfy.stop.wait(2):
                _terminate()
                return False, 'Stopped.'
        if not wait:
            return True, None
        _terminate()
        return False, f'ComfyUI did not answer within {settings.load()["comfy"]["startup_timeout"]} seconds.'
    except Exception as e:
        return False, str(e)
    finally:
        busy = ''
        db._touch()


def _terminate():
    global _proc, _log
    if _proc is not None and _proc.poll() is None:
        # ComfyUI's Python launches a child interpreter: end the whole tree.
        subprocess.run(['taskkill', '/PID', str(_proc.pid), '/T', '/F'], capture_output=True)
        try:
            _proc.wait(timeout=10)
        except Exception:
            pass
    _proc = None
    if _log:
        try:
            _log.close()
        except Exception:
            pass
        _log = None


def stop():
    """Stop the ComfyUI this app started. Returns (ok, error)."""
    global busy
    with _lock:
        if busy:
            return False, f'ComfyUI is already {busy}.'
        if not ours():
            return False, ('This ComfyUI was not started by Article2Pod (ComfyUI Desktop is running it); '
                           'close it there.' if comfy.running() else 'ComfyUI is not running.')
        busy = 'stopping'
    try:
        _terminate()
        db.log(None, 'ComfyUI stopped.')
        return True, None
    finally:
        busy = ''
        db._touch()
