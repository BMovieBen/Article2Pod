# ai_art_prompt.py
#
# Optional LLM step used by generate-art.py: reads the article title and
# the start of the (already cleaned) article text, and has the text model
# write a detailed image prompt for the podcast cover art. Runs only when
# art is actually being generated, i.e. no website / image-search art was
# found for the article (see art_sources in config.json).
#
# Controlled by the "ai_art_prompt" block in config.json (mode off | on).
# On any failure the caller falls back to the plain title template in
# art_prompt.py, so this never blocks art generation.

import os, re, time
from utils import get_temp_folder, get_ai_art_prompt_config, log_ai_detail
from ai_cleanup import run_llm, split_article

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

MARKUP_RE = re.compile(r'\[[^\]]{0,40}\]')    # [pause:3000] and similar TTS markup


def article_excerpt(slug, max_words):
    """First max_words words of the article body, or '' if there is no
    text file (audio-only items)."""
    path = os.path.join(get_temp_folder(), f'{slug}.txt')
    if not os.path.isfile(path):
        return ''
    with open(path, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    _, body, _ = split_article(text)
    words = MARKUP_RE.sub(' ', ' '.join(l.strip() for l in body if l.strip())).split()
    excerpt = ' '.join(words[:max_words])
    return excerpt + (' ...' if len(words) > max_words else '')


def build_user_prompt(title, site='', excerpt=''):
    parts = [f'TITLE: {title}']
    if site:
        parts.append(f'PUBLICATION: {site}')
    if excerpt:
        parts.append(f'ARTICLE (start):\n{excerpt}')
    else:
        parts.append('(Only the title is available.)')
    parts.append('Write the image prompt now.')
    return '\n\n'.join(parts)


# Image models draw whatever writing a prompt mentions (even "no logos"), so
# sentences that bring up text or branding are dropped from the reply.
WRITING_RE = re.compile(
    r'\b(text|texts|lettering|letters|words?|typography|captions?|headlines?|'
    r'signage|branding|brand names?|titles?|credits|watermarks?|inscriptions?)\b', re.I)

def drop_writing_sentences(text):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    kept = [s for s in sentences if not WRITING_RE.search(s)]
    return ' '.join(kept) if kept else text

def clean_reply(text):
    """Strip thinking blocks, quotes and labels the model sometimes adds,
    and any sentence that mentions writing or logos."""
    if not text:
        return ''
    text = text.split('</think>')[-1].strip()
    text = re.sub(r'^(image\s+)?prompt\s*:\s*', '', text, flags=re.I)
    text = text.strip().strip('"“”').strip()
    return drop_writing_sentences(re.sub(r'\s+', ' ', text))


def make_art_prompt(slug, title, site='', excerpt=None, cfg=None):
    """Returns (prompt, error). prompt is None when the step is off or
    failed; the caller then uses the plain title template."""
    cfg = cfg or get_ai_art_prompt_config()
    if cfg['mode'] == 'off':
        return None, 'off'
    if excerpt is None:
        excerpt = article_excerpt(slug, int(cfg['max_article_words']))
    user_prompt = build_user_prompt(title, site, excerpt)

    started = time.time()
    reply, error = run_llm(cfg, user_prompt, SYSTEM_PROMPT)
    seconds = time.time() - started
    prompt = clean_reply(reply)
    if prompt and len(prompt.split()) < 12:
        error, prompt = f'reply too short: {prompt!r}', ''

    log_ai_detail('ai-art-prompts.jsonl', {
        'slug': slug, 'title': title, 'excerpt_words': len(excerpt.split()),
        'llm_seconds': round(seconds, 1), 'prompt': prompt or None,
        'error': error or None, 'raw_reply': reply if not prompt else None,
    })
    if not prompt:
        return None, error or 'empty reply'
    print(f'  AI art prompt ({seconds:.0f}s): {prompt[:100]}...')
    return prompt, ''
