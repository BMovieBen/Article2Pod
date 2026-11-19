"""The only module that talks to ComfyUI.

ComfyUI is shared with other work, so this never touches a prompt it did
not submit: cancelling removes or interrupts only our own prompt ids, and
memory is freed only when asked.
"""
import collections, json, os, threading, time
import requests
from . import settings

stop = threading.Event()          # set by the runner's Stop; checked between polls
_own = set()                      # prompt ids we submitted and that may still be queued/running
_lock = threading.Lock()
_submitted = collections.deque(maxlen=200)   # our recent prompt ids, to tell our history entries from others'


class Result:
    def __init__(self, status, detail='', entry=None, seconds=0.0):
        self.status, self.detail, self.entry, self.seconds = status, detail, entry, seconds
        self.note = ''                # something worth putting in the job log (e.g. memory was freed first)
        self.ok = status == 'success'


def url():
    return settings.load()['comfy']['url'].rstrip('/')


def running():
    try:
        return requests.get(f'{url()}/system_stats', timeout=2).status_code == 200
    except Exception:
        return False


def queue_state():
    """(running ids, pending ids) for everything in ComfyUI's queue."""
    q = requests.get(f'{url()}/queue', timeout=5).json()
    ids = lambda key: [e[1] for e in q.get(key, []) if len(e) > 1]
    return ids('queue_running'), ids('queue_pending')


def queue_empty():
    try:
        run, pend = queue_state()
        return not run and not pend
    except Exception:
        return False


def free():
    """Ask ComfyUI to unload models. True on success."""
    try:
        return requests.post(f'{url()}/free', json={'unload_models': True, 'free_memory': True},
                             timeout=10).status_code == 200
    except Exception:
        return False


def load_workflow(name):
    """workflows/<name>.json in API format, without note entries like "_readme"."""
    path = os.path.join(settings.WORKFLOWS, f'{name}.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if isinstance(v, dict) and 'class_type' in v}


def has_workflow(name):
    return os.path.isfile(os.path.join(settings.WORKFLOWS, f'{name}.json'))


def find_node(workflow, *class_types):
    """(id, node) of the first node whose class_type is one of class_types."""
    for ct in class_types:
        for node_id, node in workflow.items():
            if node.get('class_type') == ct:
                return node_id, node
    return None, None


def _latest_history_id():
    hist = requests.get(f'{url()}/history', params={'max_items': 1}, timeout=10).json()
    return next(reversed(hist), None) if hist else None


def make_room(poll=2.0):
    """Before a speech segment: wait until nobody else's prompt is queued or
    running, then unload everything if someone else's prompt ran since our
    last one. Speech models crashed the GPU driver twice when they started
    right after another big model (see NOTES.md, 2026-11-17 and 2026-11-19).
    Returns a note for the job log ('' when nothing was done), or None when
    Stop was pressed while waiting."""
    waited = False
    while True:
        try:
            run_ids, pend_ids = queue_state()
        except Exception:
            break
        if not [i for i in run_ids + pend_ids if i not in _own]:
            break
        waited = True
        if stop.wait(poll):
            return None
    try:
        latest = _latest_history_id()
    except Exception:
        latest = None
    if waited or (latest and latest not in _submitted):
        free()
        return 'Another ComfyUI job used the GPU since our last step: freed ComfyUI memory first.'
    return ''


def run(workflow, timeout=1800, poll=1.0, fresh=False):
    """Submit a workflow and wait for it. Returns a Result whose status is
    success | error | cancelled | timeout. Result.seconds is ComfyUI's own
    execution time, so time spent queued behind other jobs is not counted.
    fresh=True: make_room() first (speech segments)."""
    if stop.is_set():
        return Result('cancelled', 'Stop requested.')
    note = ''
    if fresh:
        note = make_room()
        if note is None:
            return Result('cancelled', 'Stop requested.')
    res = _run(workflow, timeout, poll)
    res.note = note
    return res


def _run(workflow, timeout, poll):
    base = url()
    try:
        r = requests.post(f'{base}/prompt', json={'prompt': workflow}, timeout=30)
    except Exception as e:
        return Result('error', f'ComfyUI is not reachable at {base} ({e}).')
    if r.status_code != 200:
        return Result('error', f'ComfyUI rejected the workflow: {r.text[:800]}')
    pid = r.json().get('prompt_id')
    if not pid:
        return Result('error', 'ComfyUI returned no prompt_id.')
    with _lock:
        _own.add(pid)
        _submitted.append(pid)
    started, missing, unreachable, was_down = time.time(), 0, 0, False
    try:
        while time.time() - started < timeout:
            if stop.wait(poll):
                _cancel(pid)
                return Result('cancelled', 'Stop requested.')
            try:
                hist = requests.get(f'{base}/history/{pid}', timeout=10).json()
                unreachable = 0
            except Exception:
                unreachable += 1
                was_down = True
                if unreachable >= 60:
                    return Result('error', 'ComfyUI stopped responding.')
                continue
            if pid in hist:
                entry  = hist[pid]
                status = entry.get('status', {})
                secs   = _exec_seconds(entry, time.time() - started)
                if status.get('status_str') == 'error':
                    msgs = '; '.join(str(m) for m in status.get('messages', []))
                    return Result('error', f'ComfyUI error: {msgs[:1500]}', entry, secs)
                if status.get('completed'):
                    return Result('success', '', entry, secs)
            else:
                # Not in history: still queued/running, or removed by someone.
                try:
                    run_ids, pend_ids = queue_state()
                    missing = 0 if pid in run_ids or pid in pend_ids else missing + 1
                except Exception:
                    missing = 0
                if missing >= 3:
                    if was_down:          # it went away and came back without our prompt: a restart or a crash
                        return Result('error', 'ComfyUI restarted while making this (it may have crashed; '
                                               'see the ComfyUI log). Try again.')
                    return Result('cancelled', 'The job was removed from the ComfyUI queue.')
        _cancel(pid)
        return Result('timeout', f'Timed out after {timeout}s.')
    finally:
        with _lock:
            _own.discard(pid)


def _cancel(pid):
    """Remove our prompt if it is pending, interrupt it if it is the one running."""
    base = url()
    try:
        requests.post(f'{base}/queue', json={'delete': [pid]}, timeout=5)
        run_ids, _ = queue_state()
        if pid in run_ids:
            requests.post(f'{base}/interrupt', json={'prompt_id': pid}, timeout=5)
    except Exception:
        pass


def _exec_seconds(entry, fallback):
    ts = {m[0]: m[1].get('timestamp') for m in (entry or {}).get('status', {}).get('messages', [])
          if isinstance(m, (list, tuple)) and len(m) == 2 and isinstance(m[1], dict)}
    if ts.get('execution_start') and ts.get('execution_success'):
        return (ts['execution_success'] - ts['execution_start']) / 1000.0
    return fallback


def output_file(entry, key='audio'):
    """Full path of the first file a finished prompt saved (key: audio | images)."""
    for out in (entry or {}).get('outputs', {}).values():
        for item in out.get(key, []) or []:
            return os.path.join(settings.comfy_output_dir(), item.get('subfolder', ''), item['filename'])
    return None


def output_text(entry, node_id):
    """The text a PreviewAny node showed."""
    out = (entry or {}).get('outputs', {}).get(node_id, {})
    for key in ('text', 'string', 'result'):
        val = out.get(key)
        if val:
            return val[0] if isinstance(val, (list, tuple)) else str(val)
    return None
