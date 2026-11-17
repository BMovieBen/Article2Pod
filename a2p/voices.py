"""Voice samples, and the Whisper helper process used to transcribe them
and to check generated audio.

Whisper runs in asr_worker.py under ComfyUI's Python (which already has
torch and transformers), speaking one JSON object per line.
"""
import glob, json, os, random, shutil, subprocess, sys, threading
from . import settings

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'asr_worker.py')


# ---- which voice ----------------------------------------------------------
def available():
    folder = settings.load()['voices_dir']
    return sorted(os.path.basename(f) for f in glob.glob(os.path.join(folder, '*.mp3')))


def path(voice):
    return os.path.join(settings.load()['voices_dir'], voice)


def resolve(pick=''):
    """The voice file a job will use: the pick, else default_voice, else the
    first sample; "shuffle" picks at random. None when there are no samples."""
    names = available()
    if not names:
        return None
    for want in (pick, settings.load()['default_voice']):
        if want == 'shuffle':
            return random.choice(names)
        match = next((n for n in names if settings.name_key(n) == settings.name_key(want)), None) if want else None
        if match:
            return match
    return names[0]


def seconds(audio_path):
    try:
        out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                              '-of', 'csv=p=0', audio_path], capture_output=True, text=True)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


# ---- Whisper helper -------------------------------------------------------
class Whisper:
    """Start once per run, score many files, close at the end."""

    def __init__(self):
        self.proc, self.error = None, ''

    def start(self, startup_timeout=240):
        s = settings.load()
        model_dir = s['whisper_model_dir'] or ''
        if not os.path.isdir(model_dir):
            self.error = f'whisper_model_dir not found: {model_dir!r}'
            return False
        python = settings.comfy_python()
        if not os.path.isfile(python):
            python = sys.executable
        self.proc = subprocess.Popen(
            [python, WORKER, model_dir, s['quality']['device']],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', bufsize=1)
        msg = self._read(startup_timeout)
        if not msg or not msg.get('ready'):
            self.error = (msg or {}).get('error') or 'the speech model did not start'
            self.close()
            return False
        return True

    def _ask(self, request, timeout):
        if not self.proc or self.proc.poll() is not None:
            return {'error': 'the speech model is not running'}
        try:
            self.proc.stdin.write(json.dumps(request) + '\n')
            self.proc.stdin.flush()
        except Exception as e:
            return {'error': f'could not reach the speech model: {e}'}
        return self._read(timeout) or {'error': 'the speech model gave no answer'}

    def _read(self, timeout):
        result = []
        def reader():
            try:
                result.append(self.proc.stdout.readline())
            except Exception:
                result.append('')
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout)
        try:
            return json.loads(result[0]) if result and result[0] else None
        except json.JSONDecodeError:
            return None

    def score(self, audio_path, reference_text, timeout=900):
        """{match, wer, diffs, ...} comparing the audio with the text it should say."""
        return self._ask({'path': audio_path, 'reference': reference_text}, timeout)

    def transcribe(self, audio_path, max_seconds=0, timeout=600):
        return self._ask({'transcribe': audio_path, 'max_seconds': max_seconds}, timeout)

    def close(self):
        if self.proc:
            for step in (lambda: self.proc.stdin.close(),
                         lambda: (self.proc.terminate(), self.proc.wait(timeout=5))):
                try:
                    step()
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
            self.proc = None


# ---- reference sample + transcript (engines that clone from text + audio) --
def reference(voice, max_seconds=60, whisper=None, log=print):
    """(name in ComfyUI's input folder, transcript, seconds) for a voice.

    Cached next to the sample: <name>.txt is the transcript (edit it if
    Whisper misheard; it is only remade when the mp3 is replaced) and
    <name>.breeze.wav is the trimmed sample, for samples over max_seconds.
    Raises RuntimeError with a readable message when no transcript can be made."""
    src  = path(voice)
    stem = os.path.splitext(src)[0]
    txt, trimmed = stem + '.txt', stem + '.breeze.wav'
    secs  = seconds(src)
    stale = not os.path.isfile(txt) or os.path.getmtime(txt) < os.path.getmtime(src)
    trim  = secs > max_seconds
    if stale or (trim and not os.path.isfile(trimmed)):
        own = whisper is None
        if own:
            whisper = Whisper()
            log(f'Transcribing the voice sample {voice} (first use)...')
            if not whisper.start():
                raise RuntimeError(f'{voice} needs a transcript. Put it in {os.path.basename(txt)} next '
                                   f'to the mp3, or set whisper_model_dir ({whisper.error}).')
        try:
            res = whisper.transcribe(src, max_seconds - 1 if trim else 0)
        finally:
            if own:
                whisper.close()
        if res.get('error') or not res.get('text'):
            raise RuntimeError(f'Could not transcribe {voice}: {res.get("error", "empty transcript")}')
        if trim:
            cut = float(res.get('cut_seconds') or (max_seconds - 1))
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-t', f'{cut:.2f}',
                            '-ac', '1', '-ar', '24000', trimmed], check=True)
            log(f'{voice} is {secs:.0f}s; trimmed to {cut:.1f}s.')
        with open(txt, 'w', encoding='utf-8') as f:
            f.write(res['text'].strip() + '\n')
    with open(txt, 'r', encoding='utf-8') as f:
        transcript = f.read().strip()
    source = trimmed if trim else src
    if trim:
        secs = seconds(trimmed)
    return to_comfy_input(source), transcript, secs


def to_comfy_input(source):
    """Copy a file into ComfyUI's input folder (if newer) and return its name there."""
    name = os.path.basename(source)
    dest = os.path.join(settings.comfy_input_dir(), name)
    if not os.path.isfile(dest) or os.path.getmtime(dest) < os.path.getmtime(source):
        shutil.copy2(source, dest)
    return name
