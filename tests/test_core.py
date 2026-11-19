"""Unit tests for the parts that need no ComfyUI.   Run:  python -m pytest tests -q"""
import json, os
import pytest
from a2p import db, engines, migrate, sentences as S, settings
from a2p.engines import breeze


@pytest.fixture
def conf(tmp_path, monkeypatch):
    """Isolated settings file + database; returns a function that writes settings."""
    monkeypatch.setattr(settings, 'FILE', str(tmp_path / 'settings.json'))
    settings._cache.update(mtime=None, value=None)
    db.use(str(tmp_path / 'test.db'))
    def write(**over):
        settings.save(over)
        return settings.load(force=True)
    yield write
    db.use(None)
    settings._cache.update(mtime=None, value=None)


GENRES = {
    'opinion': {'narrator': 'Lively.', 'narrator_guidance': 1.25, 'quote': 'Anchor.', 'quote_guidance': 2.5},
    'news': {'narrator': 'Anchor.', 'narrator_guidance': 2.5, 'quote_cue': 'Quote:', 'quote_end_pause': 1.0},
}


# ---- settings -------------------------------------------------------------
def test_defaults_when_no_file(conf):
    assert settings.load()['port'] == 8080

def test_syntax_error_names_the_line(conf):
    with open(settings.FILE, 'w') as f:
        f.write('{\n  "port": 8080,\n  "normalize": tru\n}')
    settings._cache.update(mtime=None, value=None)
    with pytest.raises(settings.SettingsError, match='line 3'):
        settings.load()

def test_unknown_key_is_reported(conf):
    with pytest.raises(settings.SettingsError, match='guidance:'):
        conf(voices={'BillBurr.mp3': {'breeze': {'guidance:': 1.0}}})

def test_nested_engine_settings_merge(conf):
    s = conf(engines={'breeze': {'seed': 7}})
    assert s['engines']['breeze']['seed'] == 7 and s['engines']['breeze']['guidance'] == 1.5

def test_site_matching(conf):
    conf(sites={'esquire.com': {'voice': 'StephenToast.mp3'}})
    assert settings.site_entry('https://www.esquire.com/a/b')['voice'] == 'StephenToast.mp3'
    assert settings.site_entry('', 'Esquire')['voice'] == 'StephenToast.mp3'
    assert settings.site_entry('https://notesquire.com/x') == {}


# ---- Breeze voice settings precedence ---------------------------------------
def test_preset_precedence(conf):
    conf(genres=GENRES, default_genre='news', voices={'Bill': {'breeze': {
        'guidance': 1.75, 'temperature': 1.0, 'genres': {'news': {'narrator_guidance': 2.0}}}}})
    assert breeze.preset('opinion', 'Other.mp3', {})['narrator_guidance'] == 1.25      # genre
    assert breeze.preset('opinion', 'bill.mp3', {})['narrator_guidance'] == 1.75       # voice beats genre
    assert breeze.preset('news', 'Bill.mp3', {})['narrator_guidance'] == 2.0           # voice's genre beats voice
    assert breeze.preset('news', 'Bill.mp3', {'breeze': {'narrator_guidance': 3.0}})['narrator_guidance'] == 3.0
    p = breeze.preset('news', 'Bill.mp3', {})
    assert p['quote_cue'] == 'Quote:' and p['temperature'] == 1.0 and p['quote_guidance'] == 2.5

def test_preset_without_genres(conf):
    conf()
    p = breeze.preset('', 'x.mp3', {})
    assert p['narrator'] == 'Clear, natural, conversational delivery.' and p['narrator_guidance'] == 1.5

def test_max_words_shrinks_for_long_samples():
    assert breeze.max_words(30, 170) == 170
    assert breeze.max_words(90, 170) < 170


# ---- engine choice ----------------------------------------------------------
def _install(monkeypatch, *ids):
    for eid, e in engines.ALL.items():
        monkeypatch.setattr(e, 'available', lambda eid=eid: eid in ids)

def test_engine_precedence(conf, monkeypatch):
    if 'vibevoice' not in engines.ALL:
        pytest.skip('VibeVoice engine not built yet')
    _install(monkeypatch, 'vibevoice', 'breeze')
    conf(default_engine='breeze', voices={'Bill': {'engine': 'vibevoice'}})
    assert engines.resolve('', 'Other.mp3') == ('breeze', 'default')
    assert engines.resolve('', 'Bill.mp3') == ('vibevoice', 'voice')
    assert engines.resolve('Breeze', 'Bill.mp3') == ('breeze', 'picked')

def test_single_engine_always_wins(conf, monkeypatch):
    _install(monkeypatch, 'breeze')
    conf(default_engine='vibevoice')
    assert engines.resolve('vibevoice', 'x.mp3') == ('breeze', 'only engine')

def test_no_engine(conf, monkeypatch):
    _install(monkeypatch)
    assert engines.resolve('', 'x.mp3')[0] == ''


# ---- sentences and segments -------------------------------------------------
BODY = ('He paused. \u201cThis is a long quoted sentence that should count as a quote here.\u201d Garcia said.\n'
        'Plain narration follows in a new paragraph. It has two sentences.\n'
        '    A block quote line that is always a quote.')

def _sents():
    return S.mark_quotes_heuristic(S.build_sentences(S.paragraphs(BODY)))

def test_block_quotes_and_marks():
    sents = _sents()
    assert [s['quote'] for s in sents] == [False, True, False, False, False, True]
    assert sents[-1]['block_quote']

def test_segments_fold_attribution_and_add_cue():
    body = ('“This is a long quoted sentence that should count as a quote here.” Garcia said. '
            '“And here is a second long quoted sentence from the same person.”\n'
            'Plain narration follows in a new paragraph.')
    sents = S.mark_quotes_heuristic(S.build_sentences(S.paragraphs(body)))
    segs = S.segments('Title.', sents, 170, 'Quote:')
    assert [s['kind'] for s in segs] == ['title', 'quote', 'narr']
    assert segs[1]['text'].startswith('Quote: “This') and 'Garcia said.' in segs[1]['text']
    assert segs[1]['quote_start'] and segs[1]['quote_end']

def test_segments_respect_max_words():
    body = ' '.join(f'Sentence number {i} has exactly seven words.' for i in range(30))
    sents = S.build_sentences(S.paragraphs(body))
    for s in sents:
        s['quote'] = False
    segs = S.segments('', sents, 40)
    assert len(segs) > 1 and all(len(s['text'].split()) <= 40 for s in segs)
    assert ' '.join(s['text'] for s in segs) == body

def test_abbreviations_do_not_split():
    assert len(S.split_sentences('Sen. Smith met Dr. Jones today. They talked.')) == 2

def test_title_line():
    assert S.title_line('A Title', 'Jo Bloggs') == 'A Title. Written by Jo Bloggs.'
    assert S.title_line('Why?', 'Unknown Author') == 'Why?'


# ---- resume -----------------------------------------------------------------
def test_resume_keeps_finished_segments_only_for_same_plan(conf, tmp_path):
    conf()
    job = db.add_job(slug='x', title='T', author='A', site='S', text='body')
    segs = [{'kind': 'narr', 'text': f'seg {i}', 'gap': 0.4} for i in range(3)]
    db.set_plan(job, 'plan-a', segs)
    done = tmp_path / 's0.flac'
    done.write_bytes(b'x')
    db.finish_segment(job, 0, str(done), 42, 0.99, 1, 5.0)

    rows = db.set_plan(job, 'plan-a', segs)                 # same plan: segment 0 is reused
    assert [bool(r['path']) for r in rows] == [True, False, False] and rows[0]['match'] == 0.99

    rows = db.set_plan(job, 'plan-b', segs)                 # anything changed: start over
    assert not any(r['path'] for r in rows) and not done.exists()

def test_track_numbers(conf):
    conf()
    db.import_tracks({'Esquire|Charles P. Pierce': 40})
    assert db.next_track('Esquire', 'Charles P. Pierce') == 41
    assert db.next_track('New Site', 'Someone') == 1
    db.import_tracks({'Esquire|Charles P. Pierce': 5})      # never lowers a count
    assert db.next_track('Esquire', 'Charles P. Pierce') == 42


# ---- import from the old app --------------------------------------------------
def test_migrate_convert():
    old = {
        'voice_file': 'shuffle', 'tts_engine': 'breeze', 'audio_normalize_enabled': True,
        'chunk_word_count': 450, 'clipboard_domains': ['nytimes.com'],
        'ai_quality_check': {'mode': 'retry', 'asr_model_dir': 'C:\\w'},
        'breeze': {'default_genre': 'news', 'genres': GENRES, 'seed': 7,
                   'voices': {'BillBurr.mp3': {'guidance': 1.0}}},
        'voices': {'BillBurr.mp3': {'engine': 'vibevoice'}},
        'domain_overrides': {'esquire.com': {'voice_file': 'StephenToast.mp3', 'art_path': 'art\\e.jpg',
                                              'genre': 'opinion', 'breeze_guidance': 1.25}},
        'phonetic_replacements': {'Sen.': 'Senator'},
    }
    new = migrate.convert(old)
    assert new['quality_check'] is True and new['whisper_model_dir'] == 'C:\\w'
    assert new['engines'] == {'breeze': {'seed': 7}, 'vibevoice': {'chunk_words': 450}}
    assert new['voices']['BillBurr.mp3'] == {'engine': 'vibevoice', 'breeze': {'guidance': 1.0}}
    assert new['sites']['esquire.com'] == {'voice': 'StephenToast.mp3', 'art': 'art\\e.jpg', 'genre': 'opinion',
                                           'breeze': {'narrator_guidance': 1.25}}
    assert new['sites']['nytimes.com'] == {'paste_only': True}
    assert new['pronunciations'] == {'Sen.': 'Senator'}
    json.dumps(new)


# ---- VibeVoice chunks ---------------------------------------------------------
def test_vibevoice_chunks_end_at_paragraphs_and_keep_every_word():
    from a2p.engines import vibevoice
    paras = [' '.join(f'Paragraph {p} sentence {i} is here.' for i in range(8)) for p in range(10)]
    parts = vibevoice.chunks('Title. Written by A.', '\n'.join(paras), 120)
    assert len(parts) > 2 and parts[0].startswith('Title. Written by A.')
    body = '\n'.join(parts).replace('Title. Written by A.\n\n', '', 1)
    assert body.split() == ' '.join(paras).split()
    assert all(len(p.split()) <= 130 for p in parts)

def test_vibevoice_splits_an_oversized_paragraph():
    from a2p.engines import vibevoice
    big = ' '.join(f'Sentence {i} has five words.' for i in range(100))
    parts = vibevoice.chunks('', big, 100)
    assert len(parts) >= 5 and ' '.join(parts).split() == big.split()


# ---- VibeVoice per-voice settings -------------------------------------------------
def test_vibevoice_voice_settings_are_validated(conf):
    s = conf(voices={'Bill.mp3': {'engine': 'vibevoice', 'vibevoice': {'speed': 1.1, 'guidance': 1.5}}})
    assert s['voices']['Bill.mp3']['vibevoice']['speed'] == 1.1
    for bad in ({'speed': 1.5}, {'speed': 'fast'}, {'guidance': 9}, {'pitch': 1}):
        with pytest.raises(settings.SettingsError):
            conf(voices={'Bill.mp3': {'vibevoice': bad}})

def test_vibevoice_applies_speed_and_guidance(conf, monkeypatch, tmp_path):
    from a2p.engines import vibevoice
    conf(voices={'Bill.mp3': {'vibevoice': {'speed': 0.9}}}, comfy={'data_dir': str(tmp_path)})
    (tmp_path / 'input').mkdir()
    wf = {'1': {'class_type': 'LoadAudio', 'inputs': {'audio': 'x'}},
          '2': {'class_type': 'LoadTextFromFileNode', 'inputs': {'file': 'input/article.txt'}},
          '3': {'class_type': 'VibeVoiceSingleSpeakerNode', 'inputs': {'seed': 1, 'voice_speed_factor': 1.0, 'cfg_scale': 1.5}},
          '4': {'class_type': 'SaveAudioMP3', 'inputs': {'filename_prefix': 'x'}}}
    monkeypatch.setattr(vibevoice.comfy, 'load_workflow', lambda name: wf)
    monkeypatch.setattr(vibevoice.voices, 'to_comfy_input', lambda p: 'Bill.mp3')
    sent = []
    monkeypatch.setattr(vibevoice.comfy, 'run', lambda w, timeout, **k: sent.append(w) or vibevoice.comfy.Result('error', 'stop here'))
    eng = vibevoice.VibeVoice()
    plan = eng.plan({'title': 'T', 'author': 'A', 'text': 'Some words here.'}, 'Bill.mp3', lambda m: None)
    eng.render(plan, plan['segments'][0], 0, 7, str(tmp_path / 'out'))
    node = sent[0]['3']['inputs']
    assert node['voice_speed_factor'] == 0.9 and node['cfg_scale'] == 1.5 and node['seed'] == 7   # guidance: workflow's own
    assert plan['info'] == {'speed': 0.9}
