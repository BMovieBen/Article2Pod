# ai_quality.py
#
# Optional per-chunk audio quality check for generate-audio.py.
# Each generated chunk is transcribed with Whisper and compared to the text
# it was supposed to speak; if the match is below ai_quality_check.min_match
# the chunk is regenerated with a new seed, up to max_attempts, and the best
# attempt is kept.
#
# ai_quality_check.mode:
#   off       nothing runs
#   log_only  every chunk is scored and logged, never regenerated
#   retry     chunks below the threshold are regenerated
#
# Whisper runs in a helper process under ComfyUI's Python (asr_worker.py).

import os, sys, json, subprocess, threading, time
from utils import load_config, get_ai_quality_config

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'asr_worker.py')


class ASRWorker:
    def __init__(self, cfg=None):
        self.cfg  = cfg or get_ai_quality_config()
        self.proc = None
        self.error = ''

    def start(self, startup_timeout=240):
        """Returns True once the model is loaded. On failure sets .error."""
        model_dir = self.cfg.get('asr_model_dir') or ''
        if not os.path.isdir(model_dir):
            self.error = f'asr_model_dir not found: {model_dir!r}'
            return False
        python = load_config().get('comfy_venv_python') or sys.executable
        self.proc = subprocess.Popen(
            [python, WORKER, model_dir, self.cfg.get('device', 'cuda')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', bufsize=1)
        msg = self._read(startup_timeout)
        if not msg or not msg.get('ready'):
            self.error = (msg or {}).get('error') or 'ASR worker did not start'
            self.close()
            return False
        return True

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
        if not result or not result[0]:
            return None
        try:
            return json.loads(result[0])
        except json.JSONDecodeError:
            return None

    def score(self, mp3_path, reference_text, timeout=900):
        """Returns the worker's result dict (match, wer, asr_seconds, ...)
        or {'error': ...}."""
        if not self.proc or self.proc.poll() is not None:
            return {'error': 'ASR worker is not running'}
        try:
            self.proc.stdin.write(json.dumps(
                {'path': mp3_path, 'reference': reference_text}) + '\n')
            self.proc.stdin.flush()
        except Exception as e:
            return {'error': f'could not reach ASR worker: {e}'}
        return self._read(timeout) or {'error': 'ASR worker gave no answer'}

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
