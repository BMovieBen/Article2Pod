"""Clean-up and art-prompt rules, with a scripted stand-in for the text model."""
import json
import pytest
from a2p import art, cleanup, db, settings
from test_core import conf     # noqa: F401  (fixture)

OPT  = {'max_remove_fraction': 0.30, 'fix_metadata': True, 'preview_words': 22}
META = {'title': 'A long standfirst sentence that describes the story in full.', 'author': 'The Daily Site',
        'site': 'Daily Site', 'url': 'https://dailysite.com/a'}
LINES = (['Photo: Jane Doe / Agency'] +
         [f'Paragraph {i} of real reporting with enough words to count as content here.' for i in range(10)] +
         ['Sign up for our newsletter'])


def scripted(*replies):
    """An ask() that returns the given replies in order and records the prompts."""
    calls = []
    def ask(prompt, system):
        calls.append(prompt)
        r = replies[min(len(calls), len(replies)) - 1]
        return (json.dumps(r) if isinstance(r, dict) else r), ('' if r else 'no reply')
    ask.calls = calls
    return ask


def test_prompt_numbers_and_shortens_lines():
    long_line = ' '.join(f'w{i}' for i in range(60))
    p = cleanup.build_prompt(META, ['short line', long_line], 22)
    assert '[1] (2w) short line' in p and '[2] (60w) w0 ' in p and ' ... w54 w55 w56 w57 w58 w59' in p
    assert 'source: https://dailysite.com/a' in p

def test_removals_are_validated():
    picked, refused, kept = cleanup.choose_removals({'remove': [1, 12, 99, 'x', -3, 1]}, LINES, 0.30)
    assert picked == [0, 11] and refused == '' and kept == []

def test_too_many_words_is_refused():
    picked, refused, _ = cleanup.choose_removals({'remove': [2, 3, 4, 5, 6]}, LINES, 0.30)
    assert picked == [] and 'words' in refused

def test_too_many_lines_is_refused():
    lines = ['junk'] * 30 + ['a real paragraph ' * 40]
    picked, refused, _ = cleanup.choose_removals({'remove': list(range(1, 31))}, lines, 0.30)
    assert picked == [] and 'lines' in refused

def test_metadata_rules():
    ch = cleanup.choose_metadata({'title': 'Real Headline', 'author': 'Jane Doe', 'site': ''}, META)
    assert ch == {'title': 'Real Headline', 'author': 'Jane Doe'}          # empty site is ignored
    assert cleanup.choose_metadata({'title': META['title'].upper(), 'author': None, 'site': 'daily  site'}, META) == {}
    assert cleanup.choose_metadata({'title': 'x' * 300, 'author': ''}, META) == {'author': ''}   # author may be cleared

def test_decide_ok():
    d = cleanup.decide(META, LINES, ask=scripted({'remove': [1, 12], 'title': 'Real Headline',
                                                  'author': 'Jane Doe', 'site': 'Daily Site'}), options=OPT)
    assert d['status'] == 'ok' and d['remove'] == [0, 11] and not d['retried']
    assert d['metadata'] == {'title': 'Real Headline', 'author': 'Jane Doe'}

def test_decide_reasks_once_then_accepts():
    ask = scripted({'remove': list(range(1, 13))}, {'remove': [12], 'title': META['title']})
    d = cleanup.decide(META, LINES, ask=ask, options=OPT)
    assert d['status'] == 'ok' and d['remove'] == [11] and d['retried']
    assert len(ask.calls) == 2 and 'far too many lines' in ask.calls[1]

def test_decide_refuses_and_distrusts_metadata():
    ask = scripted({'remove': list(range(1, 13)), 'title': 'Hijacked'})
    d = cleanup.decide(META, LINES, ask=ask, options=OPT)
    assert d['status'] == 'refused' and d['remove'] == [] and d['metadata'] == {}

def test_decide_survives_garbage():
    d = cleanup.decide(META, LINES, ask=scripted('<think>hmm</think> I cannot do that'), options=OPT)
    assert d['status'] == 'failed' and d['remove'] == []

def test_run_applies_once_and_respects_user_edits(conf, monkeypatch):
    conf()
    reply = {'remove': [1, 12], 'title': 'Real Headline', 'author': 'Jane Doe'}
    monkeypatch.setattr(cleanup.llm, 'ask', scripted(reply))
    logs = []

    job_id = db.add_job(slug='a', title=META['title'], author=META['author'], site=META['site'],
                        text='\n'.join(LINES), info={})
    job = cleanup.run(db.get_job(job_id), logs.append)
    assert job['title'] == 'Real Headline' and job['author'] == 'Jane Doe'
    assert job['text'].split('\n') == LINES[1:11]
    assert job['info']['cleanup']['removed'] == [LINES[0], LINES[11]]
    assert job['info']['cleanup']['metadata_before']['title'] == META['title']
    assert cleanup.run(job, logs.append)['text'] == job['text']            # second call does nothing

    edited = db.add_job(slug='b', title='My Title', author='Me', site='S', text='\n'.join(LINES),
                        info={'metadata_edited': True})
    job = cleanup.run(db.get_job(edited), logs.append)
    assert job['title'] == 'My Title' and job['author'] == 'Me'            # the user's edits win
    assert job['info']['cleanup']['metadata_skipped'] and len(job['text'].split('\n')) == 10


# ---- art ------------------------------------------------------------------
def test_art_reply_cleaning():
    raw = ('<think>plan</think> Prompt: "Bold flat vector poster of a control tower at dusk. '
           'The title appears in large lettering. Warm orange palette, low viewpoint."')
    out = art.clean_reply(raw)
    assert out.startswith('Bold flat vector poster') and 'lettering' not in out and 'Warm orange' in out

def test_art_prompt_falls_back_to_title(conf):
    conf()
    job = {'title': 'The “Big” Story: Part [1]', 'site': 'S', 'text': 'word ' * 50}
    prompt, by = art.write_prompt(job, ask=scripted('too short'))
    assert by == 'title' and "Subject: \"The 'Big' Story Part 1\"" in prompt
    good = ' '.join(['Documentary photograph of an empty airport terminal at dawn, wide angle.'] * 3)
    assert art.write_prompt(job, ask=scripted(good)) == (good, 'model')

def test_art_workflow_fill():
    wf = {'4': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '{{PROMPT}} No text.'}},
          '7': {'class_type': 'KSampler', 'inputs': {'seed': 1}},
          '9': {'class_type': 'SaveImage', 'inputs': {'filename_prefix': 'x'}}}
    out = art.fill_workflow(wf, 'A scene.', 'a2p_art_5')
    assert out['4']['inputs']['text'] == 'A scene. No text.' and out['7']['inputs']['seed'] != 1
    assert out['9']['inputs']['filename_prefix'] == 'a2p_art_5'
    with pytest.raises(ValueError):
        art.fill_workflow({'4': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'fixed'}}}, 'p', 'x')

def test_art_only_when_article_has_none(conf, monkeypatch):
    conf()
    monkeypatch.setattr(art.comfy, 'has_workflow', lambda name: True)
    assert art.wanted({'info': {'art_source': 'default'}})
    assert not art.wanted({'info': {'art_source': 'page'}})
    assert not art.wanted({'info': {'art_source': 'default', 'art_tried': True}})
    conf(generate_art=False)
    assert not art.wanted({'info': {'art_source': 'default'}})


# ---- keep rules: a columnist's own lines are never page furniture ---------------
COLUMN = ['Opening paragraph of the column with plenty of words in it to count as real content here.',
          'From The Daily Bugle:',
          '    A quoted passage from another publication that runs on for a fair number of words.',
          'Wow. Who knew?',
          'Weekly Visit to the Film Archives: here is a newsreel from long ago about a parade.',
          'Is it a good day for fossil news? It is always a good day for fossil news!',
          'Read more:',
          'Photo: Jane Doe / Agency',
          'I will be back on Monday. Be well, and spare a thought for everyone having a hard week.'] + \
         [f'Body paragraph {i} with enough ordinary words in it to be counted as article content.' for i in range(12)]

def test_lines_that_introduce_a_quote_are_protected():
    assert cleanup.protected('From The New York Times:')
    assert cleanup.protected('As the AP reports, among other things:')
    assert not cleanup.protected('Read more:') and not cleanup.protected('Related stories:')
    assert not cleanup.protected('Photo: Jane Doe / Agency') and not cleanup.protected('Wow. Who knew?')

def test_keep_lines_from_settings_and_site(conf):
    conf(keep_lines=['fossil news'], sites={'example.com': {'keep_lines': ['Film Archives', r're:^I will be back on \w+day']}})
    site = settings.site_entry('https://example.com/a')
    pats = cleanup.keep_patterns(site)
    assert cleanup.protected(COLUMN[4], pats) and cleanup.protected(COLUMN[5], pats) and cleanup.protected(COLUMN[8], pats)
    assert not cleanup.protected(COLUMN[3], pats)
    assert not cleanup.protected(COLUMN[4], cleanup.keep_patterns({}))        # other sites: only the global entry

def test_model_cannot_delete_protected_lines(conf):
    conf(sites={'example.com': {'keep_lines': ['Film Archives', 'fossil news', 'back on Monday']}})
    site = settings.site_entry('https://example.com/a')
    ask = scripted({'remove': [2, 4, 5, 6, 7, 8, 9], 'title': 'Headline'})
    d = cleanup.decide(dict(META, title='Headline'), COLUMN, ask=ask, options=OPT, site=site)
    assert d['status'] == 'ok'
    assert d['remove'] == [3, 6, 7]                       # the one-liner (unprotected), "Read more:", the photo credit
    assert d['kept_by_rule'] == [1, 4, 5, 8]              # attribution, both segment intros, the sign-off

def test_a_good_headline_is_never_replaced():
    assert not cleanup.looks_wrong('The Blue Wave Made Ripples in Ways I Did Not Expect')
    assert cleanup.looks_wrong('A long standfirst sentence that describes the story in full.') and cleanup.looks_wrong('')
    meta = dict(META, title='The Blue Wave Made Ripples in Ways I Did Not Expect')
    assert cleanup.choose_metadata({'title': 'This Week in the Laboratories', 'author': META['author'], 'site': META['site']}, meta) == {}

def test_clean_up_can_be_turned_off_for_a_site(conf, monkeypatch):
    conf(sites={'example.com': {'cleanup': False}})
    monkeypatch.setattr(cleanup.llm, 'ask', lambda *a: pytest.fail('the text model must not be asked'))
    job_id = db.add_job(slug='a', url='https://www.example.com/x', title='T', author='A', site='Example',
                        text='\n'.join(COLUMN), info={})
    job = cleanup.run(db.get_job(job_id), lambda m: None)
    assert job['text'].split('\n') == COLUMN and job['info']['cleanup']['status'] == 'off for site'

def test_removed_lines_are_written_to_the_job_log(conf, monkeypatch):
    conf()
    monkeypatch.setattr(cleanup.llm, 'ask', scripted({'remove': [8], 'title': 'T'}))
    job_id = db.add_job(slug='a', title='T', author='A', site='S', text='\n'.join(COLUMN), info={})
    logs = []
    cleanup.run(db.get_job(job_id), logs.append)
    assert any(l.strip().startswith('removed: Photo: Jane Doe') for l in logs)
