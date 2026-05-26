# Article2Pod

Article2Pod converts web articles into podcast-style MP3 files. Enter a URL, and the pipeline scrapes the article text and metadata, generates audio using AI text-to-speech, embeds metadata into the MP3, and organizes the file into a destination folder structure.

It is a Python pipeline that was built for use on Windows and leverages [ComfyUI](https://www.comfy.org/) with the [VibeVoice-ComfyUI](https://github.com/Enemyx-net/VibeVoice-ComfyUI) nodes for audio generation.

Article2Pod was vibe coded using AI (primarily Anthropic's Claude model).

---

## Features

- Scrapes text, images, and metadata from most news sites using only a URL
- Falls back to a copy-paste method for sites that block scraping or are behind a paywall
- Detects and downloads embedded audio files where available instead of generating new audio
- Launches ComfyUI headlessly in the background using VibeVoice to generate MP3 files
- Embeds ID3 metadata (title, author, site, album art, track number) into each MP3
- Organizes finished files into a destination folder structure
- Batch mode — queue multiple articles before starting audio generation

---

## Prerequisites

> **Article2Pod does not install or configure ComfyUI or VibeVoice.** You are responsible for getting these working before using this tool. No support is provided for ComfyUI, VibeVoice, or their dependencies.

- **Windows 11**
- **[ComfyUI Desktop](https://www.comfy.org/download)** — installed and launched at least once
- **[VibeVoice-ComfyUI nodes](https://github.com/Enemyx-net/VibeVoice-ComfyUI)** — installed and working inside ComfyUI
- **A VibeVoice model** downloaded into `ComfyUI/models/vibevoice/`
- **Python 3.10+** — system Python (separate from ComfyUI's venv)
- **An NVIDIA GPU** — required by VibeVoice
- **ffmpeg** — installed and available on PATH. [Download here](https://ffmpeg.org/download.html) (only needed for YouTube links)
- **yt-dlp** — installed via `pip install yt-dlp` or as a standalone executable on PATH  (only needed for YouTube links)

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
| `voice_file` | Filename of your voice clone sample MP3. Must be in `input_folder`. 20-30 seconds minimum recommended. |
| `clipboard_domains` | List of domains that skip scraping and prompt for clipboard/Reader Mode paste instead. Add paywalled or bot-blocking sites here. |
| `comfy_url` | ComfyUI server URL including port. Default is `http://127.0.0.1:8000` for ComfyUI Desktop. |
| `comfy_base` | Full path to your ComfyUI installation folder. |
| `comfy_venv_python` | Full path to the Python executable inside ComfyUI's virtual environment. |
| `comfy_electron_relative` | Relative path under `%LOCALAPPDATA%` to the ComfyUI Electron installation. Only change this if you have a non-standard install. |
| `comfy_startup_timeout` | Seconds to wait for ComfyUI to become ready on startup. Increase for slower machines or larger models. |
| `workflow_file` | Path to your ComfyUI API-format workflow JSON, relative to the `article2pod` folder. |
| `audio_output_prefix` | Filename prefix for ComfyUI's audio output. Should match what is set in your workflow's SaveAudioMP3 node. |
| `input_folder` | ComfyUI input folder where your temporary `article.txt` and your voice sample live. |
| `audio_folder` | ComfyUI audio output folder where generated MP3s are written before tagging. |
| `output_folder` | ComfyUI output folder root. |
| `track_log` | Path to the track number log file, relative to the `article2pod` folder. |
| `user_agent` | User agent string used for web requests. |
| `ad_strip_markers` | List of text strings that trigger removal of everything after them in scraped articles. Useful for stripping site-specific promotional content. |

### 4. Set up the workflow

Copy the sample workflow and replace it with your own ComfyUI API-format workflow export:
```powershell
Copy-Item C:\ComfyUI\article2pod\workflow\workflow-api.sample.json C:\ComfyUI\article2pod\workflow\workflow-api.json
```

Your workflow must contain a `LoadAudio`, `LoadTextFromFileNode`, and `SaveAudioMP3` node.
Article2Pod will locate these automatically — node IDs do not matter.
See [VibeVoice-ComfyUI]https://github.com/Enemyx-net/VibeVoice-ComfyUI for information on the nodes and usage of VibeVoice within ComfyUI.

### 5. Add a voice sample

Place a voice sample MP3 in your `input_folder` and set `voice_file` in `config.json` to match the filename. At least 20-30 seconds of clean speech is recommended. Article2Pod does not condone the use of sample recordings of individuals without their written consent.

Sources for royalty-free voice samples:
- [Mozilla Common Voice](https://commonvoice.mozilla.org) — CC0 licensed
- [LibriVox](https://librivox.org) — public domain audiobooks
- Your own recording

---

## Usage

Run `article2pod.bat` directly or launch `scripts\app.py`

Finished MP3s are delivered to the `output` organized as:
```
output/
  [Site]/
    [Author]/
      article-slug.mp3
```

---

## Troubleshooting

**ComfyUI fails to start**
Check `comfy_venv_python` in `config.json` and confirm ComfyUI Desktop has been launched manually at least once. Logs are at `%APPDATA%\ComfyUI\logs\`.

**Audio generation is slow**
VibeVoice loads large models into VRAM. First-run startup is slow — increase `comfy_startup_timeout` in `config.json` if needed.

**Article scraping returns wrong or empty content**
Add the domain to `clipboard_domains` in `config.json` to force clipboard mode for that site.

**Metadata is incorrect**
Author and title detection works across most sites but some use non-standard markup. Metadata can be corrected manually after the fact in your media player.

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
- [mutagen](https://github.com/quodlibet/mutagen) by lazka and piman
- [pyperclip](https://github.com/asweigart/pyperclip) by AlSweigart