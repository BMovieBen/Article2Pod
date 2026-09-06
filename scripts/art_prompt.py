# art_prompt.py
# Builds the album-art image-gen prompt from an article title.

import re

PROMPT_TEMPLATE = (
    'No text, lettering, numbers, captions, or watermarks anywhere in the '
    'image. Professionally made image that accompanies the following news '
    'article headline "{title}". Convey the concept through people, '
    'objects, and setting rather than signs, screens, newspapers, or '
    'any other text-bearing object.'
)

def sanitize_title_for_prompt(title):
    """Clean a title for safe embedding in an image-gen prompt string.
    Converts inner double quotes to single quotes, strips smart-quote/dash
    variants down to plain ASCII, and drops characters some SDXL-family
    front-ends interpret as prompt weight/control syntax."""
    if not title:
        return ''

    replacements = {
        '\u2018': "'", '\u2019': "'",   # smart single quotes
        '\u201c': "'", '\u201d': "'",   # smart double quotes -> single
        '\u2013': '-', '\u2014': '-',   # en/em dash
        '\u2026': '...',                # ellipsis
    }
    for src, dst in replacements.items():
        title = title.replace(src, dst)

    # Inner double quotes -> single quotes (title is wrapped in double
    # quotes by the prompt template itself)
    title = title.replace('"', "'")

    # Strip characters some SDXL front-ends read as prompt control syntax
    # (weighting parens, brackets, pipe, colon, curly braces)
    title = re.sub(r'[():\[\]{}|]', '', title)

    # Collapse any doubled-up spaces left behind by stripped characters
    title = re.sub(r'\s{2,}', ' ', title).strip()

    return title

def build_art_prompt(title):
    return PROMPT_TEMPLATE.format(title=sanitize_title_for_prompt(title))
