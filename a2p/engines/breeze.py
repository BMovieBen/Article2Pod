"""Breeze TTS 2: clones from a voice sample plus its transcript and takes a
written voice direction with a guidance value. The article is read in
short segments; narration and quotes get their own direction."""
import copy, os, shutil
from .. import comfy, llm, sentences as S, settings, textclean, voices
from . import Engine


def preset(genre, voice, site):
    """Voice settings for one article. Most specific wins: the site, the
    voice's settings for this genre, the voice's own settings, the genre,
    the global engine values."""
    s   = settings.load()
    cfg = s['engines']['breeze']
    out = {'narrator': cfg['narrator'], 'quote': cfg['quote'],
           'narrator_guidance': cfg['guidance'], 'quote_guidance': cfg['quote_guidance'],
           'temperature': cfg['temperature'], 'quote_cue': '', 'quote_end_pause': cfg['switch_gap']}
    genres = {str(k).strip().lower(): v for k, v in s['genres'].items()}
    def layer(d):
        if d.get('guidance') is not None:           # shorthand: narration guidance in every genre
            out['narrator_guidance'] = d['guidance']
        out.update({k: v for k, v in d.items() if k in settings.PRESET_KEYS and v is not None})
    layer(genres.get(genre, {}))
    vb = settings.voice_entry(voice).get('breeze') or {}
    layer(vb)
    layer((vb.get('genres') or {}).get(genre) or {})
    layer(site.get('breeze') or {})
    return out


def max_words(ref_seconds, configured):
    """Breeze has a 2048-token window shared by the voice sample (12.5
    tokens a second), the text and the audio it generates: roughly 6.2
    tokens per spoken word plus ~150 for the prompt and direction."""
    return max(40, min(int(configured), int((2048 - ref_seconds * 12.5 - 150) / 6.2)))


class Breeze(Engine):
    id, label, workflow, trim = 'breeze', 'Breeze', 'breeze', True

    def first_seed(self):
        return int(settings.load()['engines']['breeze']['seed'])

    def plan(self, job, voice, log):
        cfg  = settings.load()['engines']['breeze']
        site = settings.site_entry(job.get('url') or '', job.get('site') or '')
        body = textclean.apply_phonetic_replacements(job['text'])
        sents = S.build_sentences(S.paragraphs(body))

        info = llm.analyze(job, sents, pinned_genre=job.get('genre') or site.get('genre'))
        for e in info['errors']:
            log(f'Text model problem ({e[:160]}); using fallbacks.')
        p = preset(info['genre'], voice, site)
        if '{tone}' in p['narrator']:
            p['narrator'] = p['narrator'].replace('{tone}', ', '.join(info['tone']) or 'natural')

        ref_name, ref_text, ref_secs = voices.reference(voice, float(cfg['max_reference_seconds']), log=log)
        limit = max_words(ref_secs, cfg['max_segment_words'])
        title = textclean.apply_phonetic_replacements(S.title_line(job['title'], job['author']))
        segs  = S.segments(title, sents, limit, p['quote_cue'])
        for i, seg in enumerate(segs):
            nxt = segs[i + 1] if i + 1 < len(segs) else None
            if nxt is None:
                seg['gap'] = 0.0
            elif seg['kind'] == 'title':
                seg['gap'] = float(cfg['title_gap'])
            elif seg['kind'] == 'quote' and seg['quote_end']:
                seg['gap'] = float(p['quote_end_pause'] or cfg['switch_gap'])
            elif seg['kind'] != nxt['kind']:
                seg['gap'] = float(cfg['switch_gap'])
            else:
                seg['gap'] = float(cfg['sentence_gap'])
        quotes = sum(1 for seg in segs if seg['quote_start'])
        log(f'Breeze: genre {info["genre"] or "none"} ({info["genre_by"]}), quotes by {info["quotes_by"]}, '
            f'{len(segs)} segments, {quotes} quotes, up to {limit} words each, voice sample {ref_secs:.0f}s'
            + (f', text model {info["seconds"]:.0f}s' if info['seconds'] else ''))
        return {
            'segments': segs,
            'end_pause': float(cfg['end_pause']),
            'context': {'ref_name': ref_name, 'ref_text': ref_text, 'preset': p,
                        'timeout': int(cfg['timeout'])},
            'info': {'genre': info['genre'], 'genre_by': info['genre_by'], 'quotes_by': info['quotes_by'],
                     'tone': info['tone'], 'quotes': quotes, 'text_model_seconds': info['seconds'],
                     'narrator': p['narrator'], 'narrator_guidance': p['narrator_guidance'],
                     'quote_guidance': p['quote_guidance'], 'temperature': p['temperature']},
        }

    def render(self, plan, seg, idx, seed, out_base):
        ctx, p = plan['context'], plan['context']['preset']
        wf = copy.deepcopy(comfy.load_workflow(self.workflow))
        _, load = comfy.find_node(wf, 'LoadAudio')
        if load:
            load['inputs']['audio'] = ctx['ref_name']
            load['inputs'].pop('audioUI', None)
        _, gen = comfy.find_node(wf, 'BreezeTTS2VoiceDirection', 'BreezeTTS2VoiceClone')
        _, save = comfy.find_node(wf, 'SaveAudio')
        if gen is None or save is None:
            return comfy.Result('error', 'workflows/breeze.json needs a BreezeTTS2VoiceDirection '
                                         '(or VoiceClone) node and a SaveAudio node.'), None
        quote = seg['kind'] == 'quote'
        inp = gen['inputs']
        inp.update(text=seg['text'], reference_text=ctx['ref_text'], seed=int(seed),
                   temperature=float(p['temperature']))
        if gen['class_type'] == 'BreezeTTS2VoiceDirection':
            inp['instruction'] = p['quote'] if quote else p['narrator']
            inp['cfg_scale']   = float(p['quote_guidance'] if quote else p['narrator_guidance'])
        save['inputs']['filename_prefix'] = f'audio/a2p_{os.path.basename(out_base)}'
        res = comfy.run(wf, timeout=ctx['timeout'], fresh=True)
        if not res.ok:
            return res, None
        made = comfy.output_file(res.entry, 'audio')
        if not made or not os.path.isfile(made):
            return comfy.Result('error', 'The segment finished but its audio file was not found.'), None
        dest = out_base + os.path.splitext(made)[1]
        shutil.move(made, dest)
        return res, dest
