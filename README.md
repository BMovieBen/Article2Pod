# Article2Pod

Article2Pod converts web articles into podcast-style MP3 files. Enter a URL in the web interface, and the pipeline scrapes the article text and metadata, generates audio using AI text-to-speech, embeds metadata into the MP3, and organizes the file into an output folder.

It is a Python pipeline built for Windows, using [ComfyUI](https://www.comfy.org/) with the [VibeVoice-ComfyUI](https://github.com/Enemyx-net/VibeVoice-ComfyUI) nodes for audio generation, served via a local Flask web interface.

Article2Pod was vibe coded using AI (primarily Anthropic's Claude model).

---

## Features

- Web-based interface accessible from any device on your network
- Scrapes text, images, and metadata from most news sites using only a URL
- Falls back to a paste method for sites that block scraping or are behind a paywall
- Detects and downloads embedded audio files where available instead of generating new audio
- Downloads audio from YouTube URLs via yt-dlp
- Launches ComfyUI headlessly in the background using VibeVoice to generate MP3 files
- Per-article voice sample selection, with configurable default
- Embeds ID3 metadata (title, author, site, album art, track number) into each MP3
- Organizes finished files into a structured output folder
- Library view for browsing, downloading, and deleting completed podcasts
- Queue persists across sessions — resume interrupted batches on next launch

---

## Prerequisites

> **Article2Pod does not install or configure ComfyUI or VibeVoice.** You are responsible for getting these working before using this tool. No support is provided for ComfyUI, VibeVoice, or their dependencies.

- **Windows 11**
- **[ComfyUI Desktop](https://www.comfy.org/download)** — installed and launched at least once
- **[VibeVoice-ComfyUI nodes](https://github.com/Enemyx-net/VibeVoice-ComfyUI)** — installed and working inside ComfyUI
- **A VibeVoice model** downloaded into `ComfyUI/models/vibevoice/`
- **Python 3.10+** — system Python (separate from ComfyUI's venv)
- **An NVIDIA GPU** — required by VibeVoice
- **ffmpeg** — installed and available on PATH — [Download here](https://ffmpeg.org/download.html) (required for YouTube)
- **yt-dlp** — `pip install yt-dlp` or standalone executable on PATH (required for YouTube)

### Tested Configuration

- Windows 11
- NVIDIA GeForce RTX 4070 SUPER (12GB VRAM)
- 32GB RAM
- ComfyUI Desktop v0.8.26
- VibeVoice-Large-Q4 model

---

## Installation

### 1. Clone the repository
```powershell
git clone https://github.com/BMovieBen/Article2Pod.git C:\ComfyUI\Article2Pod
```

### 2. Install Python dependencies
```powershell
pip install -r requirements.txt
```

### 3. Configure

Copy `config.sample.json` to `config.json` and update the values for your system.

| Key | Description |
|-----|-------------|
| `voice_file` | Default voice sample filename. Must exist in `voice_folder` or `input_folder`. |
| `voice_folder` | Folder containing voice sample MP3s. Defaults to `Article2Pod/voices/`. |
| `web_port` | Port the web interface runs on. Default `8080`. |
| `log_level` | Logging verbosity: `verbose`, `on_error`, or `off`. |
| `clipboard_domains` | Domains that skip scraping and use paste mode instead. Add paywalled or bot-blocking sites here. |
| `comfy_url` | ComfyUI server URL including port. Default `http://127.0.0.1:8000`. |
| `comfy_base` | Full path to your ComfyUI installation folder. |
| `comfy_venv_python` | Full path to the Python executable inside ComfyUI's virtual environment. |
| `comfy_electron_relative` | Relative path under `%LOCALAPPDATA%` to the ComfyUI Electron installation. Only change if non-standard install. |
| `comfy_startup_timeout` | Seconds to wait for ComfyUI on startup. Increase for slower machines or larger models. |
| `workflow_file` | Path to your ComfyUI API-format workflow JSON, relative to the `Article2Pod` folder. |
| `audio_output_prefix` | Filename prefix for ComfyUI audio output. Must match your workflow's SaveAudioMP3 node. |
| `input_folder` | ComfyUI input folder where `article.txt` is written and voice samples are copied before generation. |
| `audio_folder` | ComfyUI audio output folder where generated MP3s land before tagging. |
| `output_folder` | ComfyUI output folder root. |
| `track_log` | Path to the track number log, relative to the `Article2Pod` folder. |
| `user_agent` | User agent string used for web requests. |
| `ad_strip_markers` | Text strings that trigger removal of everything after them in scraped articles. |
| `phonetic_replacements` | Key-value pairs for find-and-replace in article text before TTS generation. Useful for correcting mispronunciations. |

### 4. Set up the workflow

Copy the sample workflow and replace it with your own ComfyUI API-format export:
```powershell
Copy-Item C:\ComfyUI\Article2Pod\workflow\workflow-api.sample.json C:\ComfyUI\Article2Pod\workflow\workflow-api.json
```

Your workflow must contain a `LoadAudio`, `LoadTextFromFileNode`, and `SaveAudioMP3` node. Article2Pod locates these automatically by class type — node IDs do not matter.

See [VibeVoice-ComfyUI](https://github.com/Enemyx-net/VibeVoice-ComfyUI) for details on setting up the nodes.

### 5. Add voice samples

Place voice sample MP3s in `C:\ComfyUI\Article2Pod\voices\` and set `voice_file` in `config.json` to the default filename. At least 20–30 seconds of clean speech per sample is recommended.

Article2Pod does not condone use of voice recordings of individuals without their written consent.

Sources for royalty-free voice samples:
- [Mozilla Common Voice](https://commonvoice.mozilla.org) — CC0 licensed
- [LibriVox](https://librivox.org) — public domain audiobooks
- Your own recording

### 6. Add a default art image (optional)

Place a file named `default_art.jpg` in the `Article2Pod` root folder. This is used as album art when no image can be found for an article.

---

## Usage

Double-click `article2pod.bat` or run:
```powershell
python C:\ComfyUI\Article2Pod\scripts\app.py
```

The web interface opens automatically at `http://localhost:8080` (or your configured port). It is accessible from other devices on your network via your machine's IP address.

### Adding articles

- **URL mode** — paste a URL and click `+`. The article is fetched immediately and added to the queue.
- **Text mode** — for paywalled or blocked sites, switch to Text mode, open the article in Reader Mode in your browser (`F9` in Firefox/Edge), copy all, and paste into the text box.
- If a URL is detected as blocked, the interface automatically switches to Text mode.

### Queue

- Articles queue up as you add them. Each card shows album art, title, site, and author.
- Pending items can be removed with the `-` button.
- Click the ⚙ gear icon on a pending item to select a specific voice sample for that article. The gear turns green when a non-default voice is selected.
- Click **Generate** when ready. ComfyUI starts in the background only if needed.
- YouTube URLs and articles with embedded audio skip ComfyUI entirely and download directly.
- Completed items show a download `↓` button and a delete button.

### Settings

Click the ⚙ gear in the top-right corner to set the default voice sample. Changes take effect for all subsequently queued articles.

### Library

Expand the **Library** section at the bottom of the page to browse all completed podcasts in the output folder. Each entry can be downloaded or deleted.

### Output structure
Article2Pod/output/
[Site]/
[Author]/
Site - Article Title.mp3

---

## Folder Structure
C:\ComfyUI\Article2Pod
article2pod.bat          ← launcher
config.json              ← your configuration (not in repo)
config.sample.json       ← configuration template
default_art.jpg          ← fallback album art (optional, not in repo)
requirements.txt
scripts/
app.py                 ← Flask routes
pipeline.py            ← ComfyUI management and queue processing
queue_manager.py       ← queue load/save/cleanup
web_pipeline.py        ← article add and text paste processing
fetch-article.py       ← article text scraper
fetch-metadata.py      ← metadata and album art fetcher
fetch-audio.py         ← direct audio downloader
fetch-youtube.py       ← YouTube audio downloader
generate-audio.py      ← ComfyUI API interface
tag-mp3.py             ← ID3 tagger and file mover
utils.py               ← shared utilities
templates/
index.html           ← web interface
voices/                  ← voice sample MP3s
workflow/
workflow-api.json          ← your workflow (not in repo)
workflow-api.sample.json   ← example workflow
output/                  ← finished MP3s, organized by site/author
temp/                    ← working files, cleared between sessions
log/
track-log.json

---

## Troubleshooting

**ComfyUI fails to start**
Confirm ComfyUI Desktop has been launched manually at least once. Check `comfy_venv_python` in `config.json`. Logs at `%APPDATA%\ComfyUI\logs\`.

**Audio generation is slow**
VibeVoice loads large models into VRAM on first run. Increase `comfy_startup_timeout` in `config.json` if it times out.

**Article scraping returns wrong or empty content**
Add the domain to `clipboard_domains` in `config.json` to force paste mode for that site.

**Blocked site not switching to Text mode automatically**
Add the domain to `clipboard_domains` in `config.json`.

**Metadata or album art is incorrect**
Author, title, and image detection works across most sites but some use non-standard markup. Metadata can be corrected after the fact in your media player or Plex.

**YouTube download fails**
Ensure `yt-dlp` and `ffmpeg` are installed and available on PATH. Run `yt-dlp -U` to update to the latest version.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

Copyright (c) 2026 BMovieBen

---

## Acknowledgements

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) by comfyanonymous
- [VibeVoice-ComfyUI](https://github.com/Enemyx-net/VibeVoice-ComfyUI) by Enemyx-net
- [VibeVoice](https://huggingface.co/microsoft/VibeVoice-1.5B) by Microsoft
- [readability-lxml](https://github.com/buriy/python-readability) by buriy, Tim Cutherbertson, and Sean Brant 
- [ddgs](https://github.com/deedy5/ddgs) by deedy5
- [BeautifulSoup4](https://pypi.org/project/beautifulsoup4/) by Leonard Richardson
- [Pillow](https://python-pillow.github.io/)
- [mutagen](https://github.com/quodlibet/mutagen)
- [Flask](https://flask.palletsprojects.com/)
- [Font Awesome](https://fontawesome.com/) (icons)