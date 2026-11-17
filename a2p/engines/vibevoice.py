"""VibeVoice: clones from a voice sample alone and reads long stretches of
text in one go, so an article is a few large chunks rather than many short
segments. The workflow reads its text from a file in ComfyUI's input
folder (LoadTextFromFileNode) and saves an MP3 (SaveAudioMP3)."""
import copy, glob, os, shutil
from .. import comfy, sentences as S, settings, textclean, voices
from . import Engine


def chunks(title, body, target_words):
    """Split an article into chunks of about target_words words. Chunks end
    at a paragraph break where possible; a paragraph longer than the target
    is split between sentences. The title line opens the first chunk."""
    paras = [p for p, _ in S.paragraphs(body)]
    units = []                                    # (text, starts a new paragraph)
    for p in paras:
        if len(p.split()) <= target_words:
            units.append((p, True))
        else:
            for i, sent in enumerate(S.split_sentences(p)):
                units.append((sent, i == 0))
    out, cur, words = [], [], 0
    for text, new_para in units:
        n = len(text.split())
        if cur and words + n > target_words and (new_para or words >= 0.6 * target_words):
            out.append(cur)
            cur, words = [], 0
        cur.append((text, new_para))
        words += n
    if cur:
        out.append(cur)
    rendered = []
    for c in out:
        parts = []
        for text, new_para in c:
            parts.append(text if not parts else (('\n' if new_para else ' ') + text))
        rendered.append(''.join(parts))
    if title:
        rendered = [title + '\n\n' + rendered[0]] + rendered[1:] if rendered else [title]
    return rendered


class VibeVoice(Engine):
    id, label, workflow, trim = 'vibevoice', 'VibeVoice', 'vibevoice', False

    def first_seed(self):
        _, node = comfy.find_node(comfy.load_workflow(self.workflow), 'VibeVoiceSingleSpeakerNode')
        return int((node or {}).get('inputs', {}).get('seed', 42))

    def plan(self, job, voice, log):
        cfg   = settings.load()['engines']['vibevoice']
        body  = textclean.apply_phonetic_replacements(job['text'])
        title = textclean.apply_phonetic_replacements(S.title_line(job['title'], job['author']))
        parts = chunks(title, body, int(cfg['chunk_words']))
        log(f'VibeVoice: {len(parts)} chunk(s) of up to about {cfg["chunk_words"]} words.')
        own = settings.voice_entry(voice).get('vibevoice') or {}
        if own:
            log('VibeVoice: voice settings ' + ', '.join(f'{k} {v}' for k, v in own.items()) + '.')
        return {
            'segments': [{'kind': 'chunk', 'text': t, 'gap': 0.0} for t in parts],
            'end_pause': float(cfg['end_pause']),
            'context': {'voice_name': voices.to_comfy_input(voices.path(voice)),
                        'timeout': int(cfg['timeout']),
                        'speed': own.get('speed'), 'guidance': own.get('guidance')},
            'info': {k: v for k, v in (('speed', own.get('speed')), ('vibevoice_guidance', own.get('guidance'))) if v is not None},
        }

    def render(self, plan, seg, idx, seed, out_base):
        ctx  = plan['context']
        name = os.path.basename(out_base)
        wf   = copy.deepcopy(comfy.load_workflow(self.workflow))
        _, load = comfy.find_node(wf, 'LoadAudio')
        _, text = comfy.find_node(wf, 'LoadTextFromFileNode')
        _, gen  = comfy.find_node(wf, 'VibeVoiceSingleSpeakerNode')
        _, save = comfy.find_node(wf, 'SaveAudioMP3', 'SaveAudio')
        if not (load and text and save):
            return comfy.Result('error', 'workflows/vibevoice.json needs a LoadAudio, a '
                                         'LoadTextFromFileNode and a SaveAudioMP3 node.'), None
        text_file = os.path.join(settings.comfy_input_dir(), f'a2p_{name}.txt')
        with open(text_file, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write(seg['text'])
        load['inputs']['audio']   = ctx['voice_name']
        load['inputs']['audioUI'] = f'/api/view?filename={ctx["voice_name"]}&type=input&subfolder='
        text['inputs']['file']    = f'input/a2p_{name}.txt'
        if gen:
            gen['inputs']['seed'] = int(seed)
            if ctx.get('speed') is not None:                 # the workflow's own values otherwise
                gen['inputs']['voice_speed_factor'] = float(ctx['speed'])
            if ctx.get('guidance') is not None:
                gen['inputs']['cfg_scale'] = float(ctx['guidance'])
        prefix = f'audio/a2p_{name}'
        save['inputs']['filename_prefix'] = prefix
        try:
            res = comfy.run(wf, timeout=ctx['timeout'])
        finally:
            try:
                os.remove(text_file)
            except OSError:
                pass
        if not res.ok:
            return res, None
        made = comfy.output_file(res.entry, 'audio')
        if not made or not os.path.isfile(made):       # some save nodes don't report their file
            found = sorted(glob.glob(os.path.join(settings.comfy_output_dir(), prefix + '_*')),
                           key=os.path.getmtime, reverse=True)
            made = found[0] if found else None
        if not made:
            return comfy.Result('error', 'The chunk finished but its audio file was not found.'), None
        dest = out_base + os.path.splitext(made)[1]
        shutil.move(made, dest)
        return res, dest
