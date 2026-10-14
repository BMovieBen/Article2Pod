# probe-jwplayer.py
# Experiment: can an embedded JW Player video on a page become an MP3?
# Usage: python probe-jwplayer.py <page_url> [--no-download]
#
# Steps: 1) fetch page HTML  2) find JW Player media ID
#        3) read cdn.jwplayer.com/v2/media/<id> JSON  4) pick a source
#        5) yt-dlp/ffmpeg -> MP3 in the temp folder (not the pipeline)

import os, re, sys, json, shutil, subprocess
import requests

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')

# Patterns that expose a JW Platform media ID in page HTML
ID_PATTERNS = [
    r'cdn\.jwplayer\.com/v2/media/([A-Za-z0-9]{8})',
    r'cdn\.jwplayer\.com/(?:thumbs|previews|manifests|videos)/([A-Za-z0-9]{8})',
    r'content\.jwplatform\.com/(?:players|previews|feeds|manifests|videos|thumbs)/([A-Za-z0-9]{8})',
    r'jwplatform\.com/(?:players|previews)/([A-Za-z0-9]{8})',
    r'["\']media_?id["\']\s*[:=]\s*["\']([A-Za-z0-9]{8})["\']',
    r'["\']mediaId["\']\s*[:=]\s*["\']([A-Za-z0-9]{8})["\']',
]

def find_ids(html):
    seen = []
    for pat in ID_PATTERNS:
        for m in re.finditer(pat, html):
            if m.group(1) not in seen:
                seen.append(m.group(1))
    return seen

def pick_source(sources):
    """Prefer the smallest progressive MP4 (audio only needs the track),
    then an audio-only rendition, then HLS."""
    audio = [s for s in sources if str(s.get('type', '')).startswith('audio/')]
    mp4   = [s for s in sources if s.get('type') == 'video/mp4' and s.get('file')]
    hls   = [s for s in sources if 'mpegurl' in str(s.get('type', '')).lower()]
    if audio:
        return audio[0]
    if mp4:
        return sorted(mp4, key=lambda s: s.get('height') or 9999)[0]
    if hls:
        return hls[0]
    return sources[0] if sources else None

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    url = sys.argv[1]
    download = '--no-download' not in sys.argv

    print(f'[1] Fetching page: {url}')
    r = requests.get(url, headers={'User-Agent': UA}, timeout=30)
    print(f'    HTTP {r.status_code}, {len(r.text)} chars')
    r.raise_for_status()

    ids = find_ids(r.text)
    print(f'[2] JW Player IDs found: {ids or "none"}')
    if not ids:
        print('    No JW Player ID in the raw HTML. The player may be injected by '
              'JavaScript; yt-dlp\'s generic extractor is the next thing to try:')
        print(f'      yt-dlp --dump-json --no-playlist "{url}"')
        sys.exit(2)
    mid = ids[0]

    api = f'https://cdn.jwplayer.com/v2/media/{mid}'
    print(f'[3] Reading {api}')
    j = requests.get(api, headers={'User-Agent': UA}, timeout=30)
    print(f'    HTTP {j.status_code}')
    j.raise_for_status()
    data  = j.json()
    item  = (data.get('playlist') or [{}])[0]
    title = item.get('title') or data.get('title') or mid
    print(f'    Title:       {title}')
    print(f'    Duration:    {item.get("duration")} s')
    print(f'    Description: {(item.get("description") or "")[:120]}')
    print(f'    Image:       {item.get("image")}')
    for s in item.get('sources', []):
        print(f'    source: type={s.get("type")} h={s.get("height")} '
              f'br={s.get("bitrate")} -> {s.get("file")}')

    src = pick_source(item.get('sources', []))
    if not src:
        print('[4] No usable sources.'); sys.exit(3)
    media_url = src['file']
    if media_url.startswith('//'):
        media_url = 'https:' + media_url
    print(f'[4] Chosen source: {src.get("type")} -> {media_url}')

    if not download:
        return
    if not shutil.which('ffmpeg'):
        print('    ffmpeg not on PATH'); sys.exit(4)

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(os.path.dirname(here), 'temp')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f'jwplayer-{mid}.mp3')
    print(f'[5] Extracting audio -> {out}')
    # ffmpeg handles both HLS and MP4 URLs directly
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-user_agent', UA,
           '-i', media_url, '-vn', '-ac', '2', '-q:a', '0', out]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f'    ffmpeg failed:\n{res.stderr}'); sys.exit(5)
    print(f'    Done: {os.path.getsize(out)/1_000_000:.1f} MB')

if __name__ == '__main__':
    main()
