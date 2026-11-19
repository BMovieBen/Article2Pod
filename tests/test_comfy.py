"""comfy.make_room: wait out other people's prompts, free memory when they used the GPU."""
import pytest
from a2p import comfy


@pytest.fixture
def fake(monkeypatch):
    st = {'queue': [([], [])], 'latest': None, 'freed': 0}
    def queue_state():
        return st['queue'].pop(0) if len(st['queue']) > 1 else st['queue'][0]
    monkeypatch.setattr(comfy, 'queue_state', queue_state)
    monkeypatch.setattr(comfy, '_latest_history_id', lambda: st['latest'])
    monkeypatch.setattr(comfy, 'free', lambda: st.__setitem__('freed', st['freed'] + 1) or True)
    monkeypatch.setattr(comfy, '_submitted', __import__('collections').deque(['ours1']))
    monkeypatch.setattr(comfy, '_own', set())
    comfy.stop.clear()
    return st


def test_nothing_to_do_when_our_prompt_was_last(fake):
    fake['latest'] = 'ours1'
    assert comfy.make_room() == '' and fake['freed'] == 0


def test_frees_when_someone_else_ran_since(fake):
    fake['latest'] = 'theirs'
    assert comfy.make_room() and fake['freed'] == 1


def test_waits_for_other_prompts_then_frees(fake):
    fake['queue'] = [(['theirs'], []), (['theirs'], ['ours-old']), ([], [])]
    comfy._own.add('ours-old')
    fake['latest'] = 'ours1'          # history not updated yet still counts: we waited on someone
    assert comfy.make_room(poll=0.01) and fake['freed'] == 1 and fake['queue'] == [([], [])]


def test_our_own_queued_prompts_do_not_make_us_wait(fake):
    comfy._own.add('ours2')
    fake['queue'] = [(['ours2'], [])]
    fake['latest'] = 'ours1'
    assert comfy.make_room(poll=0.01) == '' and fake['freed'] == 0


def test_stop_while_waiting(fake):
    fake['queue'] = [(['theirs'], [])]
    comfy.stop.set()
    try:
        assert comfy.make_room(poll=0.01) is None
        assert comfy.run({}, fresh=True).status == 'cancelled'
    finally:
        comfy.stop.clear()


def test_comfyui_down_is_not_an_error(fake, monkeypatch):
    def boom():
        raise OSError('down')
    monkeypatch.setattr(comfy, 'queue_state', boom)
    monkeypatch.setattr(comfy, '_latest_history_id', boom)
    assert comfy.make_room() == '' and fake['freed'] == 0
