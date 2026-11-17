"""Starting and stopping ComfyUI, with a fake process (never a real launch)."""
import os
import pytest
from a2p import comfyhost, db, runner, settings
from test_core import conf     # noqa: F401  (fixture)


class FakeProc:
    def __init__(self, exits=False):
        self.pid, self.exits, self.killed = 4242, exits, False
    def poll(self):
        return 1 if (self.exits or self.killed) else None
    def wait(self, timeout=None):
        return 0


@pytest.fixture
def host(conf, monkeypatch, tmp_path):
    """A fake ComfyUI install; returns a dict the test drives."""
    install = tmp_path / 'install'
    (install / 'ComfyUI').mkdir(parents=True)
    (install / 'ComfyUI' / 'main.py').write_text('')
    data = tmp_path / 'data'
    (data / '.venv' / 'Scripts').mkdir(parents=True)
    (data / '.venv' / 'Scripts' / 'python.exe').write_text('')
    (data / 'models' / 'checkpoints').mkdir(parents=True)
    conf(comfy={'data_dir': str(data), 'install_dir': str(install), 'startup_timeout': 2,
                'extra_args': ['--use-sage-attention'], 'url': 'http://127.0.0.1:8123'})
    monkeypatch.setattr(settings, 'DATA_DIR', str(tmp_path / 'appdata'))
    monkeypatch.setenv('APPDATA', str(tmp_path / 'roaming'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    box = {'up': False, 'launched': [], 'proc': FakeProc(), 'killed': 0, 'comes_up': True}

    def popen(args, cwd=None, **kw):
        box['launched'].append((args, cwd))
        box['up'] = box['comes_up']
        return box['proc']
    def terminate():
        box['killed'] += 1
        box['proc'].killed, box['up'] = True, False
        comfyhost._proc = None
    monkeypatch.setattr(comfyhost, '_popen', popen)
    monkeypatch.setattr(comfyhost, '_terminate', terminate)
    monkeypatch.setattr(comfyhost.comfy, 'running', lambda: box['up'])
    comfyhost.comfy.stop.clear()
    return box


def test_command_mirrors_comfyui_desktop(host, tmp_path):
    args, cwd = comfyhost.command()
    data = str(tmp_path / 'data')
    assert cwd == str(tmp_path / 'install')
    assert args[:3] == [os.path.join(data, '.venv', 'Scripts', 'python.exe'), '-s', os.path.join('ComfyUI', 'main.py')]
    for flag, value in (('--base-directory', data), ('--user-directory', os.path.join(data, 'user')),
                        ('--port', '8123'), ('--listen', '0.0.0.0'),
                        ('--input-directory', os.path.join(data, 'input')),
                        ('--output-directory', os.path.join(data, 'output'))):
        assert args[args.index(flag) + 1] == value
    assert '--use-sage-attention' in args and '--enable-manager' in args
    paths = args[args.index('--extra-model-paths-config') + 1]         # built, since Desktop wrote none
    assert "base_path: '" + os.path.join(data, 'models') + "'" in open(paths).read()

def test_desktops_own_model_paths_file_is_preferred(host, tmp_path):
    theirs = tmp_path / 'roaming' / 'Comfy Desktop' / 'instance-model-paths'
    theirs.mkdir(parents=True)
    (theirs / 'inst-1.yaml').write_text('x')
    args, _ = comfyhost.command()
    assert args[args.index('--extra-model-paths-config') + 1] == str(theirs / 'inst-1.yaml')

def test_missing_install_is_explained(host):
    settings.save({'comfy': {'install_dir': 'Z:\\nowhere', 'data_dir': settings.load()['comfy']['data_dir']}})
    with pytest.raises(RuntimeError, match='ComfyUI was not found'):
        comfyhost.command()

def test_start_and_stop_our_own(host):
    assert comfyhost.start() == (True, None)
    assert comfyhost.ours() and len(host['launched']) == 1
    assert comfyhost.start() == (True, None) and len(host['launched']) == 1     # already running: nothing launched
    assert comfyhost.stop() == (True, None) and host['killed'] == 1 and not comfyhost.ours()

def test_never_stops_someone_elses_comfyui(host):
    host['up'] = True                                   # ComfyUI Desktop has it open
    ok, error = comfyhost.stop()
    assert not ok and 'not started by Article2Pod' in error and host['killed'] == 0
    assert comfyhost.status() == {'running': True, 'ours': False, 'busy': ''}

def test_start_reports_a_crash_and_a_timeout(host):
    host['comes_up'], host['proc'] = False, FakeProc(exits=True)
    ok, error = comfyhost.start()
    assert not ok and 'exited while starting' in error
    host['proc'] = FakeProc()
    ok, error = comfyhost.start()
    assert not ok and 'did not answer' in error and host['killed'] >= 1


# ---- the queue ---------------------------------------------------------------
def _article():
    return db.add_job(slug='a', kind='article', title='T', author='A', site='S', text='word ' * 50)

def test_queue_starts_comfyui_then_stops_what_it_started(host, monkeypatch):
    ran = []
    monkeypatch.setattr(runner, 'run_job', lambda job_id, box: (ran.append(job_id), db.update_job(job_id, status='done')))
    job = _article()
    runner.run_queue()
    assert ran == [job] and len(host['launched']) == 1 and host['killed'] == 1

def test_queue_leaves_a_running_comfyui_alone(host, monkeypatch):
    host['up'] = True
    monkeypatch.setattr(runner, 'run_job', lambda job_id, box: db.update_job(job_id, status='done'))
    _article()
    runner.run_queue()
    assert host['launched'] == [] and host['killed'] == 0

def test_queue_gives_up_cleanly_when_comfyui_will_not_start(host, monkeypatch):
    host['comes_up'], host['proc'] = False, FakeProc(exits=True)
    monkeypatch.setattr(runner, 'run_job', lambda job_id, box: pytest.fail('must not run an article'))
    job = _article()
    runner.run_queue()
    assert db.get_job(job)['status'] == 'pending' and len(host['launched']) == 1     # tried once, not in a loop

def test_start_when_needed_can_be_turned_off(host, monkeypatch):
    settings.save(dict(comfy=dict(settings.load()['comfy'], start_when_needed=False)))
    job = _article()
    runner.run_queue()
    assert host['launched'] == [] and db.get_job(job)['status'] == 'pending'
