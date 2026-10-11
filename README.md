# Article2Pod

Turns web articles into podcast MP3s. Paste a URL, it grabs the text, reads it out with [ComfyUI](https://www.comfy.org/) + [VibeVoice](https://github.com/Enemyx-net/VibeVoice-ComfyUI), tags the MP3 and files it away. Runs as a local web page you can open from any device on your network.

Windows only. Vibe coded, mostly with Claude.

## What it does

- Scrapes text, author and art from most news sites. Paywalled or blocked? Paste the text instead.
- Grabs embedded audio and YouTube links directly, no generating.
- Pick a voice per article, or shuffle.
- Art comes from the site, an image search, or gets auto-generated with ComfyUI.

## Preprequisite

I don't install or support any of this, get it working first:

- [ComfyUI Desktop](https://www.comfy.org/download) (run it once) with the [VibeVoice-ComfyUI](https://github.com/Enemyx-net/VibeVoice-ComfyUI) nodes and a VibeVoice model in `ComfyUI/models/vibevoice/`
- NVIDIA GPU
- [Python](https://www.python.org/downloads/) 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) and [yt-dlp](https://github.com/yt-dlp/yt-dlp) on PATH (YouTube only)

Tested on Windows 11, RTX 4070 Super 12GB, 32GB RAM, ComfyUI Desktop 0.8.26, VibeVoice-Large-Q4.

## Setup

1. Clone it and install requirements:
   ```powershell
   git clone https://github.com/BMovieBen/Article2Pod.git C:\ComfyUI\Article2Pod
   pip install -r requirements.txt
   ```
2. Copy `config.sample.json` to `config.json` and fill it in. Every setting is explained in the file.
3. Copy `workflow\workflow-api.sample.json` to `workflow-api.json`, or swap in your own API-format export. It needs a `LoadAudio`, `LoadTextFromFileNode` and `SaveAudioMP3` node.
4. Drop voice sample MP3s in `voices\` (20-30 seconds of clean speech). Only use voices you have permission to use. [Common Voice](https://commonvoice.mozilla.org) and [LibriVox](https://librivox.org) are good free sources.
5. Optional: put a `default_art.jpg` in the root for fallback album art.

## Using it

Run `article2pod.bat`. The page opens at `http://localhost:8080`.

- **URL:** paste it and hit `+`.
- **Text:** open the article in Reader Mode (F9), copy everything, paste it in. Blocked sites switch to this on their own.
- Gear on a queue item picks its voice. Top-right gear sets the default.
- Hit **Generate**. ComfyUI only starts if it's needed.

Finished files land in `output\[Site]\[Author]\`.

## Art generation

Optional. If the site has no usable image, Article2Pod can have ComfyUI make album art from the article title.

1. Copy `workflow\art-workflow-api.sample.json` to `art-workflow-api.json`, or use your own text-to-image workflow (API-format export).
2. In `config.json`, set `art_workflow_file` and make sure `comfyui_generate` is in `art_sources`. The order of that list is the order sources get tried.

## Troubleshooting

- **ComfyUI won't start:** launch ComfyUI Desktop by hand once, check `comfy_venv_python`, then look at `%APPDATA%\ComfyUI\logs\`.
- **Timeout on first run:** the model is loading. Raise `comfy_startup_timeout`.
- **Bad or empty scrape:** add the site to `clipboard_domains`.
- **YouTube fails:** run `yt-dlp -U`.

## License

MIT, see [LICENSE](LICENSE).

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
