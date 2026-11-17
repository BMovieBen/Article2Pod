"""Cover art generated with ComfyUI, for articles that came with no image.

The text model writes an image prompt from the title and the start of
the article; if that fails a plain template built from the title is used.
The prompt replaces the {{PROMPT}} token in workflows/art.json.
"""
import os, random, re
from . import comfy, db, fetch, llm, settings

TOKEN = '{{PROMPT}}'

SYSTEM_PROMPT = """You write prompts for a text-to-image model. The image is square cover art for a podcast episode in which an article is read aloud.

You are given the article's title, publication and the start of the article. Write ONE prompt describing a single striking, purely graphical image that complements the article. The articles cover many subjects, not only news.

Rules:
- First decide what the article is about, then pick ONE fitting visual approach and commit to it, stating the style first. For example:
  * video games: an in-game style render or key art of the game's world, characters or vehicles, in the game's own art style;
  * movies and TV: theatrical key art or a cinematic promo still of the actors or characters in a memorable scene;
  * music: album-cover style art or a live concert photograph;
  * technology and products: a polished studio product shot or a scene of the product in use;
  * news, politics, business, science: a realistic documentary or press photograph, or a highly stylised artwork (bold flat vector poster, risograph print, papercut collage, oil painting, ink and watercolour, 3D clay render, retro pulp illustration).
  Vary the approach between articles; do not default to a moody grey editorial photo.
- Prefer showing the actual subject over abstract symbols: the game's own world and characters, the film's characters and setting, the real people involved. Use metaphor only when there is nothing concrete to show.
- Describe a concrete scene: the main subject, setting, a few key objects, composition, viewpoint, lighting and colour palette.
- Real, identifiable people may be shown and named when they are central to the story (for example the politician, actor, musician or executive the article is about); describe them doing something that fits the story.
- The picture must contain no writing, so leave out titles, credits and lettering even for poster-style art, and avoid things that normally carry writing: documents, newspapers, book covers, screens with interfaces, signs, banners, labels, or brand names. Do not mention text, words, or letters at all in the prompt.
- Describe only what is visible. No explanations of what things symbolise and no remarks about thumbnails or podcasts.
- Keep the main subject large and centred.
- No gore, graphic nudity or graphic violence.
- 50 to 100 words, plain English, a single paragraph.

Reply with ONLY the prompt text, no title, no quotes, no commentary."""

TEMPLATE = ("Visual representation of the below topic without any text in the final image. It's more "
            "important to generate an image that emphasizes key subjects and themes and does not need "
            "to be a literal interpretation. This is intended to be used as artwork to accompany a "
            'podcast about the below subject.\n\nSubject: "{title}"')

# Image models draw whatever writing a prompt mentions (even "no logos"), so
# sentences that bring up text or branding are dropped from the model's reply.
WRITING_RE = re.compile(
    r'\b(text|texts|lettering|letters|words?|typography|captions?|headlines?|'
    r'signage|branding|brand names?|titles?|credits|watermarks?|inscriptions?)\b', re.I)


def clean_reply(text):
    """Strip thinking blocks, labels and quotes, and any sentence about writing."""
    if not text:
        return ''
    text = text.split('</think>')[-1].strip()
    text = re.sub(r'^(image\s+)?prompt\s*:\s*', '', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text.strip().strip('"“”').strip())
    kept = [s for s in re.split(r'(?<=[.!?])\s+', text) if not WRITING_RE.search(s)]
    return ' '.join(kept) if kept else text


def title_prompt(title):
    """The fallback prompt: the title, made safe to embed in a prompt string."""
    for src, dst in {'‘': "'", '’': "'", '“': "'", '”': "'",
                     '–': '-', '—': '-', '…': '...', '"': "'"}.items():
        title = title.replace(src, dst)
    title = re.sub(r'\s{2,}', ' ', re.sub(r'[():\[\]{}|]', '', title)).strip()
    return TEMPLATE.format(title=title)


def write_prompt(job, ask=None):
    """(prompt, by) where by is 'model' or 'title'."""
    ask = ask or llm.ask
    s = settings.load()
    if s['art_prompt']:
        words   = ' '.join(l.strip() for l in (job.get('text') or '').split('\n')).split()
        limit   = int(s['art']['excerpt_words'])
        excerpt = ' '.join(words[:limit]) + (' ...' if len(words) > limit else '')
        parts = [f'TITLE: {job["title"]}']
        if job.get('site'):
            parts.append(f'PUBLICATION: {job["site"]}')
        parts.append(f'ARTICLE (start):\n{excerpt}' if excerpt else '(Only the title is available.)')
        parts.append('Write the image prompt now.')
        reply, _ = ask('\n\n'.join(parts), SYSTEM_PROMPT)
        prompt = clean_reply(reply)
        if len(prompt.split()) >= 12:
            return prompt, 'model'
    return title_prompt(job['title'] or ''), 'title'


def fill_workflow(workflow, prompt, prefix):
    """Put the prompt where {{PROMPT}} is, reseed, and name the output."""
    hit = False
    for node in workflow.values():
        inputs = node.get('inputs', {})
        for key in ('text', 'value'):
            if isinstance(inputs.get(key), str) and TOKEN in inputs[key]:
                inputs[key] = inputs[key].replace(TOKEN, prompt)
                hit = True
        if 'seed' in inputs and not isinstance(inputs['seed'], list):
            inputs['seed'] = random.randint(1, 2**50)      # a new image every time
        if node.get('class_type') == 'SaveImage':
            inputs['filename_prefix'] = prefix
    if not hit:
        raise ValueError('workflows/art.json has no {{PROMPT}} token in a prompt node.')
    return workflow


def wanted(job):
    """Generate only when the article has no art of its own."""
    s = settings.load()
    return (s['generate_art'] and comfy.has_workflow('art')
            and job['info'].get('art_source') in (None, 'default')
            and not job['info'].get('art_tried'))


def run(job, log):
    """Generate art for a job (once). Never fails the job. Returns the refreshed job."""
    if not wanted(job):
        return job
    db.merge_info(job['id'], art_tried=True)
    try:
        prompt, by = write_prompt(job)
        if llm.loaded:
            comfy.free()                       # drop the text model before the image model loads
            llm.loaded = False
        wf  = fill_workflow(comfy.load_workflow('art'), prompt, f'a2p_art_{job["id"]}')
        res = comfy.run(wf, timeout=int(settings.load()['art']['timeout']))
        made = comfy.output_file(res.entry, 'images') if res.ok else None
        if not made or not os.path.isfile(made):
            log(f'Cover art not generated ({res.detail or "no image file"}); using the default art.')
            return db.get_job(job['id'])
        from PIL import Image
        img   = Image.open(made).convert('RGB')
        scale = max(500 / img.width, 500 / img.height)
        img   = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        left, top = (img.width - 500) // 2, (img.height - 500) // 2
        dest = os.path.join(fetch.job_dir(job['id']), 'art.jpg')
        img.crop((left, top, left + 500, top + 500)).save(dest, 'JPEG', quality=90)
        os.remove(made)
        db.update_job(job['id'], art_path=dest)
        db.merge_info(job['id'], art_source='generated', art_prompt=prompt, art_prompt_by=by,
                      art_seconds=round(res.seconds, 1))
        log(f'Cover art generated in {res.seconds:.0f}s (prompt by {by}): {prompt[:90]}...')
    except Exception as e:
        log(f'Cover art not generated ({e}); using the default art.')
    finally:
        comfy.free()                           # the image model must not sit in memory during TTS
        if comfy.stop.is_set():
            db.merge_info(job['id'], art_tried=False)      # stopped mid-way: try again next run
    return db.get_job(job['id'])
