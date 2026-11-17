"""Safety net for the whole suite: unit tests must never launch or stop a
real ComfyUI, whatever they exercise. Tests that cover the launcher replace
these stand-ins with their own fakes."""
import pytest
from a2p import comfyhost, settings


@pytest.fixture(autouse=True)
def no_real_comfyui(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'DATA_DIR', str(tmp_path / 'appdata'))     # logs, previews, yaml
    def refuse(*args, **kwargs):
        raise RuntimeError('tests must not launch ComfyUI')
    monkeypatch.setattr(comfyhost, '_popen', refuse)
    monkeypatch.setattr(comfyhost, '_terminate', lambda: None)
    monkeypatch.setattr(comfyhost, '_proc', None)
    monkeypatch.setattr(comfyhost, 'busy', '')
    yield
