# fetch-jwplayer.py
# Downloads the audio of a JW Player video and converts it to MP3.
# Usage: python fetch-jwplayer.py <media_id> <slug>
# The sidecar JSON/art for the slug is already written by fetch-article.py.

import os, sys, shutil, subprocess
from utils import get_audio_folder
from jwplayer import get_media, pick_source
from utils import CHROME_UA

AUDIO_FOLDER = get_audio_folder()

def fetch_jwplayer(media_id, slug):
    if not shutil.which('ffmpeg'):
        print('  ffmpeg is required but not found on PATH.')
        sys.exit(1)

    media = get_media(media_id)
    if not media:
        print(f'  Could not read JW Player media {media_id}.')
        sys.exit(1)
    src = pick_source(media['sources'])
    if not src:
        print(f'  No downloadable source for JW Player media {media_id}.')
        sys.exit(1)
    url = src['file']
    if url.startswith('//'):
        url = 'https:' + url
    print(f'  Source:   {src.get("type")} {url}')

    os.makedirs(AUDIO_FOLDER, exist_ok=True)
    dest = os.path.join(AUDIO_FOLDER, f'{slug}.mp3')
    part = os.path.join(AUDIO_FOLDER, f'{slug}.part.mp3')
    # ffmpeg reads HLS, MP4 and M4A URLs directly
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-user_agent', CHROME_UA,
           '-i', url, '-vn', '-ac', '2', '-q:a', '0', part]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.isfile(part):
        print(f'  Download failed: {res.stderr}')
        if os.path.isfile(part):
            os.remove(part)
        sys.exit(1)
    os.replace(part, dest)
    print(f'  Downloaded: {dest}')
    return dest

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python fetch-jwplayer.py <media_id> <slug>')
        sys.exit(1)
    try:
        fetch_jwplayer(sys.argv[1], sys.argv[2])
    except SystemExit:
        raise
    except Exception as e:
        print(f'Error: {e}')
        sys.exit(1)
