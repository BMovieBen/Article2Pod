# Article2Pod

Turns web articles into podcast MP3s. Paste a URL, it grabs the text, reads it out with [ComfyUI](https://www.comfy.org/) + [Breeze TTS 2](https://github.com/saganaki22/ComfyUI-Breeze-TTS-2) or [VibeVoice](https://github.com/Enemyx-net/VibeVoice-ComfyUI), tags the MP3 and files it away. Runs as a local web page you can open from any device on your network.

Windows only. Vibe coded, mostly with Claude.

## What it does

- Scrapes text, author and art from most news sites. Paywalled or blocked? Paste the text from Reader Mode instead.
- Downloads YouTube videos, JW Player video pages and pages with their own MP3 as audio, no generating. Reads Steam news posts.
- Pick a voice and an engine per article, or shuffle. Mix Breeze and VibeVoice in one queue.
- Optional text-model clean-up removes leftover page junk and fixes a wrong title, author or site.
- Cover art from the site, or generated with ComfyUI when there is none.
- Stop the queue at any time: finished segments are kept and the article resumes where it stopped.
- A library of every MP3, a player, and statistics on which voices need retries most.

## Prerequisites

I don't install or support any of this, get it working first:

- [ComfyUI Desktop](https://www.comfy.org/download) (run it once) with the [Breeze TTS 2](https://github.com/saganaki22/ComfyUI-Breeze-TTS-2) nodes and/or the [VibeVoice-ComfyUI](https://github.com/Enemyx-net/VibeVoice-ComfyUI) nodes and a VibeVoice model
- NVIDIA GPU
- [Python](https://www.python.org/downloads/) 3.13 (3.11+ works; yt-dlp no longer supports 3.10)
- [ffmpeg](https://ffmpeg.org/download.html) on PATH

Tested on Windows 11, RTX 4070 Super 12GB, 32GB RAM.

## Setup

1. Clone it:
   ```powershell
   git clone https://github.com/BMovieBen/Article2Pod.git C:\ComfyUI\Article2Pod
   ```
   The first run of `article2pod.bat` creates the app's own Python environment in `.venv` (with `py -3.13`) and installs `requirements.txt` into it.
2. Copy `settings.sample.json` to `settings.json`. Every setting is optional except the genres (Breeze reads each genre with its own voice direction). Most settings can then be changed on the Settings tab.
3. In `workflow\`, copy the sample workflows you want to use:
   - `breeze.sample.json` to `breeze.json` for Breeze TTS 2. On ComfyUI 0.44 or later, version 1.4.7 of the Breeze nodes needs the 4-line fix from [PR #15](https://github.com/saganaki22/ComfyUI-Breeze-TTS-2/pull/15).
   - `vibevoice.sample.json` to `vibevoice.json` for VibeVoice (or save your own export under that name; it needs a `LoadAudio`, `LoadTextFromFileNode` and `SaveAudioMP3` node).
   - `text.sample.json` to `text.json` for the text model (clean-up, genre and quote detection, art prompts).
   - `art.sample.json` to `art.json` for generated cover art.

   An engine is offered only when its workflow is there; with one, every article uses it.
4. Drop voice sample MP3s in `voices\` (20-30 seconds of clean speech). Only use voices you have permission to use. Breeze also needs a transcript of each sample: set `whisper_model_dir` and it is made on first use (`voices\<name>.txt`; edit it if Whisper misheard).
5. Optional: put a `default_art.jpg` in this folder for articles with no art.

## Using it

Run `article2pod.bat`. The page opens at `http://localhost:8080`.

- **Queue:** paste a URL (or switch to Text and paste from Reader Mode) and Add. The gear on a card picks its voice and engine and edits its title, author and site; click or drop an image on the picture, or use the clipboard badge, to change the art. Hit **Generate**; ComfyUI is started if it isn't running.
- **Library:** every MP3 in `output\`, with play, edit (renames and moves the file), download and delete. "retries (N)" means N segments were re-generated because their first take failed the quality check.
- **Stats:** which voices need retries most, engine speed and quality, recent failures.
- **Settings:** voices (engine, guidance or speed, per-genre overrides, Preview with the settings as they are on the page, saved or not), genres, sites, pronunciations, ComfyUI start/stop, and everything else.

Finished files land in `output\[Site]\[Author]\`.

## How Breeze reads an article

- The text model picks a genre from the ones in your settings and the sentences that are quoted speech. It only returns labels and never rewrites text; its quote picks are checked against the actual quotation marks.
- Each genre has a narration direction, a quote direction and a guidance value for each (how strongly the direction is applied: low keeps the voice sample's own personality).
- Most specific wins: the site > the voice's settings for that genre > the voice > the genre > the global values.

Breeze model weights are licensed for research and non-commercial use only.

## For developers

| Path | What it is |
|---|---|
| `a2p/settings.py` | `settings.json`, merged over built-in defaults and validated |
| `a2p/db.py`, `a2p/stats.py` | SQLite store (`data\a2p.db`): queue, segments, track numbers, logs, statistics |
| `a2p/comfy.py`, `a2p/comfyhost.py` | Talking to ComfyUI; starting and stopping it |
| `a2p/sources/` | Source plug-ins: web page, Steam news, YouTube, JW Player, embedded audio |
| `a2p/engines/` | TTS engine plug-ins: Breeze, VibeVoice |
| `a2p/cleanup.py`, `a2p/llm.py`, `a2p/art.py` | Text-model clean-up, genre and quotes, art prompt and generated art |
| `a2p/runner.py`, `a2p/audio.py` | The queue (stop and resume); join, normalize, tag, file |
| `a2p/web.py`, `a2p/static/` | The web page and its JSON API |
| `tests/` | `.venv\Scripts\python -m pytest tests -q` (`pip install pytest` into `.venv` first) |

**Adding a source:** a module in `a2p/sources/` with a `Source` subclass (`matches` and `fetch`), added to the order in `sources/__init__.py` ahead of `WebPage`.
**Adding an engine:** a module in `a2p/engines/` with an `Engine` subclass (`plan` and `render`), its workflow in `workflow\`, listed in `engines/__init__.py`. Segments, the quality check, stop and resume then work for it unchanged.

A local, untracked `NOTES.md` (ignored by git) can hold your own running log of decisions and gotchas.

## Troubleshooting

- **The page says it cannot reach Article2Pod:** start it with `article2pod.bat`.
- **ComfyUI won't start:** open ComfyUI Desktop by hand once; its log is in `data\comfyui-console.log` when Article2Pod started it.
- **A site won't scrape:** paste the text, and mark the site "Blocks scraping" on the Settings tab.
- **YouTube fails:** yt-dlp is updated automatically once per start; if it still fails, run `.venv\Scripts\python -m pip install -U yt-dlp`.

## License

MIT, see [LICENSE](LICENSE).

## Acknowledgements

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) by comfyanonymous
- [ComfyUI-Breeze-TTS-2](https://github.com/saganaki22/ComfyUI-Breeze-TTS-2) by saganaki22
- [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2) by BreezeBlue
- [VibeVoice-ComfyUI](https://github.com/Enemyx-net/VibeVoice-ComfyUI) by Enemyx-net
- [VibeVoice](https://huggingface.co/microsoft/VibeVoice-1.5B) by Microsoft
- [trafilatura](https://github.com/adbar/trafilatura) by Adrien Barbaresi
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [BeautifulSoup4](https://pypi.org/project/beautifulsoup4/) by Leonard Richardson
- [Pillow](https://python-pillow.github.io/)
- [mutagen](https://github.com/quodlibet/mutagen)
- [Flask](https://flask.palletsprojects.com/)
- [Font Awesome](https://fontawesome.com/) (icons)
