# asr_worker.py
#
# Long-lived speech-to-text helper for the optional audio quality check.
# Runs under ComfyUI's Python (it has torch + transformers) so Article2Pod's
# own environment needs no extra packages. Started by ai_quality.ASRWorker.
#
# Protocol: one JSON object per line on stdin, one JSON reply per line on
# stdout (everything else goes to stderr).
#   start-up  ->  {"ready": true}   or   {"ready": false, "error": "..."}
#   {"path": "chunk.mp3", "reference": "text that should have been spoken"}
#       -> {"match": 0.97, "wer": 0.03, "ref_words": 1403, "asr_seconds": 6.2,
#           "transcript_words": 1399}
#
# Usage: python asr_worker.py <whisper_model_dir> <cuda|cpu>

import re, difflib, sys, json, time, os

real_stdout = sys.stdout
sys.stdout  = sys.stderr          # library chatter must not corrupt the protocol


def reply(obj):
    real_stdout.write(json.dumps(obj) + '\n')
    real_stdout.flush()


def edit_distance(a, b):
    """Word-level Levenshtein distance (two-row DP)."""
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, wa in enumerate(a, 1):
        cur = [i]
        for j, wb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wa != wb)))
        prev = cur
    return prev[-1]


def canon(tokens):
    """Smooth over differences that are formatting, not mispronunciation:
    $ % , signs, "no 2" vs "number 2", "a i" vs "ai", "23 2026" vs "232026"."""
    out = []
    for t in tokens:
        t = t.replace('%', '').replace('$', '').replace(',', '')
        if t:
            out.append(t)
    for i, t in enumerate(out):
        if t == 'no' and i + 1 < len(out) and out[i + 1].isdigit():
            out[i] = 'number'
    merged = []
    for t in out:
        if merged and ((len(t) == 1 and t.isalpha() and len(merged[-1]) == 1 and merged[-1].isalpha())
                       or (t.isdigit() and merged[-1].isdigit())):
            merged[-1] += t
        else:
            merged.append(t)
    return merged


def count_errors(ref, hyp):
    """Word errors between two token lists. A replaced stretch whose words
    are the same once spaces are ignored (sell off / selloff) is not an error."""
    errors = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ref, hyp, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        if tag == 'replace' and ''.join(ref[i1:i2]) == ''.join(hyp[j1:j2]):
            continue
        errors += max(i2 - i1, j2 - j1)
    return errors


def main():
    model_dir, device = sys.argv[1], sys.argv[2]
    try:
        import torch
        from transformers import pipeline
        from transformers.models.whisper.english_normalizer import EnglishTextNormalizer

        if device == 'cuda' and not torch.cuda.is_available():
            device = 'cpu'
        dtype = torch.float16 if device == 'cuda' else torch.float32
        asr = pipeline('automatic-speech-recognition', model=model_dir,
                       torch_dtype=dtype, device=device, chunk_length_s=30,
                       batch_size=8)
        # Keep the speech model in system RAM while idle: VibeVoice needs
        # nearly all of a 12 GB card, and sharing it makes generation crawl.
        if device == 'cuda':
            asr.model.to('cpu')
            torch.cuda.empty_cache()
        mapping_path = os.path.join(model_dir, 'normalizer.json')
        with open(mapping_path, 'r', encoding='utf-8') as f:
            normalizer = EnglishTextNormalizer(json.load(f))
    except Exception as e:
        reply({'ready': False, 'error': f'{type(e).__name__}: {e}'})
        return

    reply({'ready': True, 'device': device})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req   = json.loads(line)
            start = time.time()
            if device == 'cuda':
                asr.model.to('cuda')
            try:
                out = asr(req['path'], return_timestamps=True,   # without timestamps long audio can lose whole 30s windows
                          generate_kwargs={'language': 'en', 'task': 'transcribe'})
            finally:
                if device == 'cuda':
                    asr.model.to('cpu')
                    torch.cuda.empty_cache()
            secs  = time.time() - start
            # Whisper's normalizer deletes anything in (), [] or <> -- but the
            # voice reads parenthetical sentences aloud, so keep that text.
            reference = re.sub(r'[()\[\]<>]', ' ', req['reference'])
            ref   = canon(normalizer(reference).split())
            hyp   = canon(normalizer(out['text']).split())
            wer   = count_errors(ref, hyp) / max(1, len(ref))
            diffs = []
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ref, hyp, autojunk=False).get_opcodes():
                if tag != 'equal' and len(diffs) < 60:
                    diffs.append({'op': tag, 'ref': ' '.join(ref[max(0, i1 - 2):i2 + 2])[:200],
                                  'heard': ' '.join(hyp[max(0, j1 - 2):j2 + 2])[:200]})
            reply({'diffs': diffs, 'match': round(max(0.0, 1.0 - wer), 4), 'wer': round(wer, 4),
                   'ref_words': len(ref), 'transcript_words': len(hyp), 'transcript_head': out['text'][:160],
                   'asr_seconds': round(secs, 2)})
        except Exception as e:
            reply({'error': f'{type(e).__name__}: {e}'})


if __name__ == '__main__':
    main()
