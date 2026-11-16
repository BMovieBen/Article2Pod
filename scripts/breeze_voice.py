# breeze_voice.py
#
# Breeze TTS 2 clones a voice from a sample plus the sample's exact
# transcript, and accepts at most 60 seconds of sample. This prepares both
# the first time a voice is used with Breeze and caches them next to the
# voice mp3, so it happens once per voice:
#   voices/<name>.txt            transcript (plain text, edit it if Whisper misheard)
#   voices/<name>.breeze.wav     trimmed sample, only for samples over the limit
# The transcript is redone if the mp3 is replaced (newer than the .txt).
# Whisper comes from ai_quality_check.asr_model_dir, the same model the
# optional quality check uses (it does not need that check to be on).

import os, shutil, subprocess, json
from utils import get_input_folder, get_ai_quality_config

def audio_seconds(path):
    try:
        out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                              '-of', 'csv=p=0', path], capture_output=True, text=True)
        return float(out.stdout.strip())
    except Exception:
        return 0.0

def _paths(voice_full_path):
    stem = os.path.splitext(voice_full_path)[0]
    return stem + '.txt', stem + '.breeze.wav'

def prepare_reference(voice_file, voice_full_path, max_seconds=60, worker=None):
    """Returns (input_audio_name, transcript, seconds) for the Breeze
    workflow, copying the sample into ComfyUI's input folder. Raises
    RuntimeError with a readable message when no transcript can be made."""
    txt_path, trimmed_path = _paths(voice_full_path)
    seconds = audio_seconds(voice_full_path)
    stale   = (not os.path.isfile(txt_path) or
               os.path.getmtime(txt_path) < os.path.getmtime(voice_full_path))
    needs_trim = seconds > max_seconds

    if stale or (needs_trim and not os.path.isfile(trimmed_path)):
        own_worker = worker is None
        if own_worker:
            from ai_quality import ASRWorker
            worker = ASRWorker(get_ai_quality_config())
            print('  Breeze voice: transcribing the voice sample (first use)...')
            if not worker.start():
                raise RuntimeError(
                    f'Breeze needs a transcript of the voice sample. Either put it in '
                    f'{os.path.basename(txt_path)} next to the mp3, or set '
                    f'ai_quality_check.asr_model_dir to a Whisper model ({worker.error}).')
        try:
            res = worker.transcribe(voice_full_path, max_seconds - 1 if needs_trim else 0)
        finally:
            if own_worker:
                worker.close()
        if res.get('error') or not res.get('text'):
            raise RuntimeError(f'Breeze voice: could not transcribe {voice_file}: '
                               f'{res.get("error", "empty transcript")}')
        if needs_trim:
            cut = float(res.get('cut_seconds') or (max_seconds - 1))
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', voice_full_path, '-t', f'{cut:.2f}',
                            '-ac', '1', '-ar', '24000', trimmed_path], check=True)
            print(f'  Breeze voice: sample is {seconds:.0f}s, trimmed to {cut:.1f}s '
                  f'-> {os.path.basename(trimmed_path)}')
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(res['text'].strip() + '\n')
        print(f'  Breeze voice: transcript saved to {os.path.basename(txt_path)} '
              f'(edit it if anything is misheard).')

    with open(txt_path, 'r', encoding='utf-8') as f:
        transcript = f.read().strip()
    source = trimmed_path if needs_trim else voice_full_path
    if needs_trim:
        seconds = audio_seconds(trimmed_path)
    input_name = os.path.basename(source)
    dest = os.path.join(get_input_folder(), input_name)
    if (not os.path.isfile(dest)) or os.path.getmtime(dest) < os.path.getmtime(source):
        shutil.copy2(source, dest)
    return input_name, transcript, seconds

def max_words_for_reference(ref_seconds, configured_max):
    """Breeze has a 2048-token context shared by the voice sample (12.5
    tokens per second), the text and the generated audio. Roughly 6.2
    tokens per spoken word, plus ~150 for the prompt and direction."""
    budget = 2048 - ref_seconds * 12.5 - 150
    return max(40, min(int(configured_max), int(budget / 6.2)))
