# Article2Pod

Turns web articles into podcast MP3s. Paste a URL, it grabs the text, reads it out with [ComfyUI](https://www.comfy.org/) + [VibeVoice](https://github.com/Enemyx-net/VibeVoice-ComfyUI) or [Breeze TTS 2](https://github.com/saganaki22/ComfyUI-Breeze-TTS-2), tags the MP3 and files it away. Runs as a local web page you can open from any device on your network.

Windows only. Vibe coded, mostly with Claude.

## What it does

- Scrapes text, author and art from most news sites. Paywalled or blocked? Paste the text instead.
- Grabs embedded audio and YouTube links directly, no generating.
- Pick a voice per article, or shuffle.
- Art comes from the site, an image search, or gets auto-generated with ComfyUI.

## Preprequisite

I don't install or support any of this, get it working first:

- [ComfyUI Desktop](https://www.comfy.org/download) (run it once) with either the [VibeVoice-ComfyUI](https://github.com/Enemyx-net/VibeVoice-ComfyUI) nodes and a VibeVoice model in `ComfyUI/models/vibevoice/`, or the [Breeze TTS 2](https://github.com/saganaki22/ComfyUI-Breeze-TTS-2) nodes (see [Breeze TTS 2](#breeze-tts-2))
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
2. Create `config.json`. Every setting is optional, and folders default to ComfyUI Desktop's standard locations, so `{}` works on a standard install. `config.sample.json` explains every setting and its default; copy over only the ones you want to change.
3. Set up one or both voice engines by copying their sample workflow in `workflow\`:
   - `workflow-api.vibevoice.sample.json` to `workflow-api.vibevoice.json` for VibeVoice. Your own export works too; it needs a `LoadAudio`, `LoadTextFromFileNode` and `SaveAudioMP3` node.
   - `workflow-api.breeze.sample.json` to `workflow-api.breeze.json` for Breeze TTS 2. It needs a `LoadAudio`, `BreezeTTS2VoiceDirection` and `SaveAudio` node.

   With only one of them, every article uses it. With both, you pick per article (see [Voice engines](#voice-engines)).
4. Drop voice sample MP3s in `voices\` (20-30 seconds of clean speech). Only use voices you have permission to use. [Common Voice](https://commonvoice.mozilla.org) and [LibriVox](https://librivox.org) are good free sources.
5. Optional: put a `default_art.jpg` in the root for fallback album art.

## Using it

Run `article2pod.bat`. The page opens at `http://localhost:8080`.

- **URL:** paste it and hit `+`.
- **Text:** open the article in Reader Mode (F9), copy everything, paste it in. Blocked sites switch to this on their own.
- Gear on a queue item picks its voice. Top-right gear sets the default.
- Hit **Generate**. ComfyUI only starts if it's needed.

Finished files land in `output\[Site]\[Author]\`.

## Voice engines

With both workflows installed, VibeVoice and Breeze jobs can share one queue. Which engine an article uses, first match wins:

1. The engine picked in the card's gear.
2. The site's `engine` in `domain_overrides` (set when the article is added; the card can change it).
3. The voice's engine, from the top-level `voices` block in `config.json`, e.g. `"voices": { "YourVoice.mp3": { "engine": "vibevoice" } }`. With shuffle this is decided once the voice is picked.
4. The default engine (`tts_engine`), which the top-right gear also sets.

Each card shows a small badge with the engine it will use (Auto when a shuffled voice decides) and, once done, the one it used. `log\generation-log.csv` has an engine column.

When the engine changes between two jobs, Article2Pod frees ComfyUI's memory first so both models aren't loaded at once, but only when ComfyUI has no other jobs queued. Set `"group_by_engine": true` to run all queued articles for the loaded engine before switching, instead of strict queue order.

## Breeze TTS 2

An alternative to VibeVoice with more natural intonation. It reads each article in short pieces with a voice direction that suits the article, so a news story gets a news-anchor read and a column keeps the voice sample's personality.

**Setup**

1. Install the [ComfyUI-Breeze-TTS-2](https://github.com/saganaki22/ComfyUI-Breeze-TTS-2) nodes. The model (int8 hybrid, about 5 GB) downloads on first use into `ComfyUI/models/breezetts2/`.
   - On ComfyUI 0.44 or later, version 1.4.7 of the nodes fails with `'_ComfyLinear' object has no attribute '_combined_linear'`. Until it is merged, apply the 4-line fix from [PR #15](https://github.com/saganaki22/ComfyUI-Breeze-TTS-2/pull/15).
2. Copy `workflow\workflow-api.breeze.sample.json` to `workflow\workflow-api.breeze.json`.
3. Set `ai_quality_check.asr_model_dir` to a Whisper model, even if the quality check itself is off. Breeze needs an exact transcript of each voice sample, and Article2Pod makes one the first time a voice is used (`voices\<name>.txt`; edit it if Whisper misheard something). Samples over 60 seconds are trimmed at a sentence end (`voices\<name>.breeze.wav`).
4. The genre and quote detection use the same text-model workflow as `ai_cleanup` (`workflow\cleanup-workflow-api.json`, or the sample if you have none), whether or not cleanup itself is on. Set `breeze.genre_mode` and `breeze.quote_mode` to `"off"` / `"marks"` to skip the text model.

**How an article is read**

- The text model picks a genre from `breeze.genres` in `config.json` (the sample ships opinion, news, entertainment, tech_gaming, feature, humor and interview; add or change them freely) and the sentences that are quoted speech. It only returns labels and never rewrites text. Its quote picks are checked against the actual quotation marks, and indented block quotes always count as quotes.
- Each genre has a narrator direction, a quote direction and a guidance value for each. Guidance is how strongly the direction is applied: low keeps the voice sample's own personality, high follows the direction more closely.
  - **opinion:** lively narration at guidance 1.25, quotes in a news-anchor voice.
  - **news:** news-anchor voice throughout; quotes are introduced with a spoken "Quote:" and followed by a short pause.
  - The other genres have starting-point directions that have not been tuned by ear yet.
- The genres and their directions live only in `config.json`; with no genres every article gets one plain direction. Change any of it in the `breeze` block of `config.json`, per voice in `breeze.voices` (for a voice the defaults don't suit, overall or for one genre), or per site in `domain_overrides` (for example pin `esquire.com` to `"genre": "opinion"`).
- What was decided for each article is written to `temp\<slug>.breeze.json` and `log\breeze-articles.csv`.

Breeze model weights are licensed for research and non-commercial use only.

## Art generation

Optional. If the site has no usable image, Article2Pod can have ComfyUI make album art from the article title.

1. Copy `workflow\art-workflow-api.sample.json` to `art-workflow-api.json`, or use your own text-to-image workflow (API-format export).
2. In `config.json`, set `art_workflow_file` and make sure `comfyui_generate` is in `art_sources`. The order of that list is the order sources get tried.
3. Optional: turn on `ai_art_prompt`. The text model from `ai_cleanup` then reads the title and the start of the cleaned article and writes a detailed image prompt, which replaces the `{{PROMPT}}` token in the art workflow's prompt node. If the step fails, the plain title template is used instead. Prompts are logged to `log/ai-art-prompts.jsonl`. The shipped `art-workflow-api.sample.json` is a MOSS-Image SFT workflow set up this way; it shares its Qwen 3.5 9B text encoder with the `ai_cleanup` text model.

## AI cleanup and quality check

Both are optional, off in `config.sample.json`, and slow generation down, so switch them off with `"mode": "off"` when speed matters.

- **`ai_cleanup`** runs after you click Generate. A text model in ComfyUI picks leftover junk lines such as captions, promos and repeated bylines, and checks the title, author and site. It only chooses lines to delete and never rewrites the article. Metadata you edited yourself is left alone. Modes: `off`, `log_only` (decide and log, change nothing), `apply`. If the model lists too many lines it is asked once more with a stricter prompt, and a reply that would remove more than `max_remove_fraction` (default 0.30) of the article is refused and the text is left alone.
- **`ai_quality_check`** transcribes each generated chunk with Whisper (`asr_model_dir`) and compares it with the chunk's text, ignoring bracketed text and number or spelling differences that sound the same. In `retry` mode a chunk scoring under `min_match` is regenerated with a new seed, up to `max_attempts`, and the best attempt is kept. `log_only` scores and logs without regenerating. Whisper sits on the CPU between scores so it does not slow generation.
- **Changing the text model:** copy `workflow\cleanup-workflow-api.sample.json` to `cleanup-workflow-api.json` (`ai_cleanup.workflow_file`) and edit the `CLIPLoader` node there to use a different text encoder. Keep the `{{prompt}}` and `{{system_prompt}}` placeholders and a `PreviewAny` node, which the reply is read from.
- **Chunk size:** `chunk_word_count` (default 1400) sets how much text is generated at a time. Smaller chunks (400-500 words, about 5 minutes of audio) lose less work when a chunk fails or is retried. Chunks end at a paragraph break where possible.
- **Stats** go to `log/` when `ai_stats_logging` is on: `ai-cleanup-stats.csv` and `ai-cleanup-details.jsonl` (what was removed or changed), `ai-quality-chunks.csv` (every attempt), `ai-quality-details.jsonl` (the words Whisper heard differently) and `ai-quality-articles.csv` (per article time and score). `ai-quality-chunks.csv` includes the voice used, so grouping it by voice shows which voice samples fail the check most. Compare against `generation-log.csv` for speed with the features off.

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
- [ComfyUI-Breeze-TTS-2](https://github.com/saganaki22/ComfyUI-Breeze-TTS-2) by saganaki22
- [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2) by BreezeBlue
- [readability-lxml](https://github.com/buriy/python-readability) by buriy, Tim Cutherbertson, and Sean Brant
- [ddgs](https://github.com/deedy5/ddgs) by deedy5
- [BeautifulSoup4](https://pypi.org/project/beautifulsoup4/) by Leonard Richardson
- [Pillow](https://python-pillow.github.io/)
- [mutagen](https://github.com/quodlibet/mutagen)
- [Flask](https://flask.palletsprojects.com/)
- [Font Awesome](https://fontawesome.com/) (icons)
