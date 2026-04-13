# fetch-youtube.py
# Downloads audio from a YouTube URL, converts to MP3
# Usage: python fetch-youtube.py <url> <slug>

import os, sys, json, subprocess, shutil
from utils import get_audio_folder, get_temp_folder, get_user_agent

AUDIO_FOLDER = get_audio_folder()
TEMP_FOLDER  = get_temp_folder()

_ytdlp_updated_this_session = False

def check_dependencies():
    """Verify yt-dlp and ffmpeg are available on PATH."""
    # Check ffmpeg — must be installed manually
    if not shutil.which('ffmpeg'):
        print(f'  ffmpeg is required but not found on PATH.')
        print(f'  Please install it from https://ffmpeg.org/download.html')
        sys.exit(1)

    # Check yt-dlp — offer to install via pip if missing
    if not shutil.which('yt-dlp'):
        print(f'  yt-dlp is not found on PATH.')
        answer = input('  Install yt-dlp via pip now? [Y/N]: ').strip().upper()
        if answer == 'Y':
            print('  Installing yt-dlp...')
            result = subprocess.run(
                ['python', '-m', 'pip', 'install', 'yt-dlp'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print('  yt-dlp installed successfully.')
            else:
                print(f'  Installation failed: {result.stderr}')
                sys.exit(1)
        else:
            print(f'  yt-dlp is required to process YouTube URLs.')
            sys.exit(1)

def update_ytdlp():
    """Check for and apply yt-dlp updates once per session."""
    global _ytdlp_updated_this_session
    if _ytdlp_updated_this_session:
        return
    _ytdlp_updated_this_session = True

    print('  Checking for yt-dlp updates...')
    result = subprocess.run(
        ['yt-dlp', '-U'],
        capture_output=True,
        text=True
    )
    output = (result.stdout + result.stderr).lower()
    if 'up to date' in output:
        print('  yt-dlp is up to date.')
    elif 'updated' in output or 'updating' in output:
        print('  yt-dlp updated successfully.')
    else:
        # Fallback: try pip upgrade
        subprocess.run(
            ['python', '-m', 'pip', 'install', '-U', 'yt-dlp'],
            capture_output=True
        )
        print('  yt-dlp update attempted via pip.')

def get_video_metadata(url):
    """Fetch video metadata without downloading using yt-dlp --dump-json."""
    result = subprocess.run(
        ['yt-dlp', '--dump-json', '--no-playlist', url],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f'  Failed to fetch metadata: {result.stderr}')
        sys.exit(1)
    return json.loads(result.stdout)

def download_audio(url, slug):
    """Download audio from YouTube and convert to MP3 via ffmpeg."""
    dest = os.path.join(AUDIO_FOLDER, f'{slug}.mp3')
    print(f'  Downloading audio...')

    result = subprocess.run([
        'yt-dlp',
        '--no-playlist',
        '--extract-audio',
        '--audio-format', 'mp3',
        '--audio-quality', '0',
        '--output', dest,
        url
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f'  Download failed: {result.stderr}')
        sys.exit(1)

    # yt-dlp may append extension even if specified — find the actual file
    if not os.path.isfile(dest):
        import glob
        matches = glob.glob(os.path.join(AUDIO_FOLDER, f'{slug}*.mp3'))
        if matches:
            os.rename(matches[0], dest)
        else:
            print(f'  Could not find downloaded file for slug: {slug}')
            sys.exit(1)

    print(f'  Downloaded: {dest}')
    return dest

def fetch_youtube(url, slug):
    check_dependencies()
    update_ytdlp()

    # Get metadata
    print(f'  Fetching video metadata...')
    meta = get_video_metadata(url)

    title     = meta.get('title', 'Untitled')
    channel   = meta.get('channel', meta.get('uploader', 'Unknown Channel'))
    playlist  = meta.get('playlist_title')
    album     = playlist if playlist else channel
    thumbnail = meta.get('thumbnail')

    print(f'  Title:    {title}')
    print(f'  Channel:  {channel}')
    print(f'  Album:    {album}')

    # Download thumbnail as album art
    art_path = None
    if thumbnail:
        try:
            import requests
            from PIL import Image
            from io import BytesIO

            r   = requests.get(thumbnail, timeout=10)
            img = Image.open(BytesIO(r.content)).convert('RGB')

            target_w, target_h = 500, 500
            orig_w, orig_h     = img.size
            scale              = max(target_w / orig_w, target_h / orig_h)
            scaled_w           = int(orig_w * scale)
            scaled_h           = int(orig_h * scale)
            img                = img.resize((scaled_w, scaled_h), Image.LANCZOS)
            left               = (scaled_w - target_w) // 2
            top                = (scaled_h - target_h) // 2
            img                = img.crop((left, top, left + target_w, top + target_h))

            art_path = os.path.join(TEMP_FOLDER, f'{slug}.jpg')
            img.save(art_path, 'JPEG', quality=90)
            print(f'  Art:      {art_path}')
        except Exception as e:
            print(f'  Art:      failed ({e})')

    # Write metadata JSON for tag-mp3.py
    meta_out = {
        'title':      title,
        'artist':     channel,
        'album':      album,
        'album_art':  art_path,
        'slug':       slug,
        'source_url': url,
    }
    json_path = os.path.join(TEMP_FOLDER, f'{slug}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(meta_out, f, indent=2, ensure_ascii=False)
    print(f'  Meta:     {json_path}')

    # Download audio
    download_audio(url, slug)

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python fetch-youtube.py <url> <slug>')
        sys.exit(1)
    try:
        fetch_youtube(sys.argv[1], sys.argv[2])
    except Exception as e:
        print(f'Error: {e}')
        sys.exit(1)