"""TTS engines are plug-ins. To add one: write a module here with a class
that subclasses Engine, and list it in ALL below. The runner only ever
uses this interface, so segments, the quality check, stop and resume work
the same for every engine.
"""
from .. import comfy, settings


class Engine:
    id       = ''     # used in settings ("breeze")
    label    = ''     # shown in the app ("Breeze")
    workflow = ''     # workflows/<workflow>.json; the engine is available when it exists
    trim     = True   # trim silence from the ends of each segment before joining

    def available(self):
        return comfy.has_workflow(self.workflow)

    def plan(self, job, voice, log):
        """Decide how the article is read. Returns a dict:
             segments   [{'kind', 'text', 'gap'}]  gap = seconds of silence after it
             end_pause  seconds of silence at the very end
             context    anything render() needs (voice reference, directions...)
             info       facts worth recording on the job (genre, ...)
           Everything in segments + context shapes the audio: if it is all
           unchanged, finished segments are reused when a job resumes."""
        raise NotImplementedError

    def first_seed(self):
        return 42

    def render(self, plan, seg, idx, seed, out_base):
        """Generate one segment. Returns (comfy.Result, path of the audio).
        out_base is a path without extension to move the file to."""
        raise NotImplementedError


def _all():
    from .breeze import Breeze
    engines = [Breeze()]
    try:
        from .vibevoice import VibeVoice
        engines.insert(0, VibeVoice())
    except ImportError:
        pass
    return {e.id: e for e in engines}


ALL = _all()


def normalize(name):
    """'Breeze', 'VibeVoice ' -> engine id, else ''."""
    n = ''.join(c for c in str(name or '').lower() if c.isalpha())
    for eid in ALL:
        if n and (n == eid or eid.startswith(n) or n.startswith(eid)):
            return eid
    return ''


def available():
    return [eid for eid, e in ALL.items() if e.available()]


def default():
    avail = available()
    want  = normalize(settings.load()['default_engine'])
    return want if want in avail else (avail[0] if avail else '')


def resolve(pick='', voice=''):
    """(engine id, reason). With one engine installed it is always used;
    otherwise first match wins: the job's pick (the card, or the site when
    the article was added), the voice's engine, the default engine."""
    avail = available()
    if not avail:
        return '', 'no engine workflow installed'
    if len(avail) == 1:
        return avail[0], 'only engine'
    for eid, reason in ((normalize(pick), 'picked'),
                        (normalize(settings.voice_entry(voice).get('engine')), 'voice')):
        if eid in avail:
            return eid, reason
    return default(), 'default'
