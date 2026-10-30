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

## AI cleanup and quality check

Both are optional, off in `config.sample.json`, and slow generation down, so switch them off with `"mode": "off"` when speed matters.

- **`ai_cleanup`** runs after you click Generate. A text model in ComfyUI picks leftover junk lines such as captions, promos and repeated bylines, and checks the title, author and site. It only chooses lines to delete and never rewrites the article. Metadata you edited yourself is left alone. Modes: `off`, `log_only` (decide and log, change nothing), `apply`. If the model lists too many lines it is asked once more with a stricter prompt, and a reply that would remove more than `max_remove_fraction` (default 0.30) of the article is refused and the text is left alone.
- **`ai_quality_check`** transcribes each generated chunk with Whisper (`asr_model_dir`) and compares it with the chunk's text, ignoring bracketed text and number or spelling differences that sound the same. In `retry` mode a chunk scoring under `min_match` is regenerated with a new seed, up to `max_attempts`, and the best attempt is kept. `log_only` scores and logs without regenerating. Whisper sits on the CPU between scores so it does not slow generation.
- **Changing the text model:** copy `workflow\cleanup-workflow-api.sample.json` to `cleanup-workflow-api.json` (`ai_cleanup.workflow_file`) and edit the `CLIPLoader` node there to use a different text encoder. Keep the `{{prompt}}` and `{{system_prompt}}` placeholders and a `PreviewAny` node, which the reply is read from.
- **Chunk size:** `chunk_word_count` (default 1400) sets how much text is generated at a time. Smaller chunks (400-500 words, about 5 minutes of audio) lose less work when a chunk fails or is retried. Chunks end at a paragraph break where possible.
- **Stats** go to `log/` when `ai_stats_logging` is on: `ai-cleanup-stats.csv` and `ai-cleanup-details.jsonl` (what was removed or changed), `ai-quality-chunks.csv` (every attempt), `ai-quality-details.jsonl` (the words Whisper heard differently) and `ai-quality-articles.csv` (per article time and score). Compare against `generation-log.csv` for speed with the features off.

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
