"""Import from an original-Article2Pod config.json (before the rewrite):
builds settings.json from it and carries the track numbers over. Only
needed when moving an old install; it never overwrites an existing
settings.json unless asked."""
import json, os
from . import db, settings


def strip_json_comments(text):
    out, in_str, esc, i = [], False, False, 0
    while i < len(text):
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == '/' and text[i + 1:i + 2] == '/':
            while i < len(text) and text[i] != '\n':
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return ''.join(out)


BREEZE_GLOBAL = ('guidance', 'quote_guidance', 'temperature', 'seed', 'narrator', 'quote',
                 'max_segment_words', 'max_reference_seconds', 'sentence_gap', 'switch_gap',
                 'title_gap', 'end_pause')
SITE_BREEZE = {'breeze_narrator': 'narrator', 'breeze_guidance': 'narrator_guidance',
               'breeze_quote': 'quote', 'breeze_quote_guidance': 'quote_guidance',
               'breeze_quote_cue': 'quote_cue', 'breeze_temperature': 'temperature'}


def convert(old):
    """Old config dict -> new settings overrides (only what differs from defaults)."""
    new = {}
    def put(key, value):
        if value not in (None, '', {}, []):
            new[key] = value
    put('default_voice', old.get('voice_file'))
    put('default_engine', old.get('tts_engine'))
    if old.get('group_by_engine'):
        new['group_by_engine'] = True
    if 'audio_normalize_enabled' in old:
        new['normalize'] = bool(old['audio_normalize_enabled'])
    qc = old.get('ai_quality_check') or {}
    new['quality_check'] = qc.get('mode', 'off') == 'retry'
    put('whisper_model_dir', qc.get('asr_model_dir'))
    quality = {k: qc[k] for k in ('min_match', 'max_attempts', 'device') if k in qc}
    put('quality', quality)

    breeze  = old.get('breeze') or {}
    engines = {}
    b = {k: breeze[k] for k in BREEZE_GLOBAL if k in breeze}
    if b:
        engines['breeze'] = b
    if 'chunk_word_count' in old:
        engines['vibevoice'] = {'chunk_words': old['chunk_word_count']}
    put('engines', engines)
    if breeze.get('genre_mode') == 'off' and breeze.get('quote_mode') in ('marks', 'off'):
        new['text_model'] = False
    put('default_genre', breeze.get('default_genre'))
    put('genres', breeze.get('genres'))

    voices = {}
    for name, entry in (old.get('voices') or {}).items():
        if entry.get('engine'):
            voices.setdefault(name, {})['engine'] = entry['engine']
    for name, entry in (breeze.get('voices') or {}).items():
        voices.setdefault(name, {})['breeze'] = entry
    put('voices', voices)

    sites = {}
    for domain, entry in (old.get('domain_overrides') or {}).items():
        s = {}
        for src, dst in (('voice_file', 'voice'), ('art_path', 'art'), ('engine', 'engine'), ('genre', 'genre')):
            if entry.get(src):
                s[dst] = entry[src]
        sb = {dst: entry[src] for src, dst in SITE_BREEZE.items() if entry.get(src) is not None}
        if sb:
            s['breeze'] = sb
        sites[domain] = s
    for domain in old.get('clipboard_domains') or []:
        sites.setdefault(domain, {})['paste_only'] = True
    put('sites', sites)

    cl = old.get('ai_cleanup') or {}
    new['cleanup'] = cl.get('mode', 'off') == 'apply'
    opts = {dst: cl[src] for src, dst in (('max_remove_fraction', 'max_remove_fraction'),
                                          ('fix_metadata', 'fix_metadata'),
                                          ('max_preview_words', 'preview_words')) if src in cl}
    put('cleanup_options', opts)
    new['art_prompt'] = (old.get('ai_art_prompt') or {}).get('mode', 'off') == 'on'
    sources = old.get('art_sources', ['website', 'comfyui_generate', 'default'])
    new['generate_art'] = 'comfyui_generate' in sources
    if 'art_generation_timeout' in old:
        new['art'] = {'timeout': old['art_generation_timeout']}
    put('pronunciations', old.get('phonetic_replacements'))
    put('strip_lines', old.get('strip_lines'))
    put('ad_markers', old.get('ad_strip_markers'))
    comfy = {dst: old[src] for src, dst in (('comfy_url', 'url'), ('comfy_extra_args', 'extra_args'),
                                            ('comfy_startup_timeout', 'startup_timeout'),
                                            ('comfy_listen_host', 'listen')) if src in old}
    put('comfy', comfy)
    return new


def run(overwrite=False):
    old_file = os.path.join(settings.ROOT, 'config.json')
    if os.path.isfile(settings.FILE) and not overwrite:
        print(f'{settings.FILE} already exists; not overwriting.')
    elif os.path.isfile(old_file):
        with open(old_file, 'r', encoding='utf-8') as f:
            old = json.loads(strip_json_comments(f.read()))
        settings.save(convert(old))
        settings.load(force=True)      # validates
        print(f'Wrote {settings.FILE} from {old_file}')
    track_log = os.path.join(settings.ROOT, 'log', 'track-log.json')
    if os.path.isfile(track_log):
        with open(track_log, 'r', encoding='utf-8') as f:
            tracks = json.load(f)
        db.import_tracks(tracks)
        print(f'Imported {len(tracks)} track counters.')
