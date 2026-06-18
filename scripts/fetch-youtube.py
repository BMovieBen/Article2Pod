# fetch-youtube.py
# Downloads audio from a YouTube URL, converts to MP3
# Usage: python fetch-youtube.py <url> <slug>

import os, sys, json, subprocess, shutil, glob
from utils import get_audio_folder, get_temp_folder, fetch_and_resize_image

AUDIO_FOLDER = get_audio_folder()
TEMP_FOLDER  = get_temp_folder()

_ytdlp_updated_this_session = False

def check_dependencies():
    if not shutil.which('ffmpeg'):
        print('  ffmpeg is required but not found on PATH.')
        print('  Please install it from https://ffmpeg.org/download.html')
        sys.exit(1)
    if not shutil.which('yt-dlp'):
        print('  yt-dlp is not found on PATH.')
        answer = input('  Install yt-dlp via pip now? [Y/N]: ').strip().upper()
        if answer == 'Y':
            result = subprocess.run(['python', '-m', 'pip', 'install', 'yt-dlp'],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                print(f'  Installation failed: {result.stderr}')
                sys.exit(1)
            print('  yt-dlp installed successfully.')
        else:
            print('  yt-dlp is required to process YouTube URLs.')
            sys.exit(1)

def update_ytdlp():
    global _ytdlp_updated_this_session
    if _ytdlp_updated_this_session:
        return
    _ytdlp_updated_this_session = True
    print('  Checking for yt-dlp updates...')
    result = subprocess.run(['yt-dlp', '-U'], capture_output=True, text=True)
    output = (result.stdout + result.stderr).lower()
    if 'up to date' in output:
        print('  yt-dlp is up to date.')
    elif 'updated' in output or 'updating' in output:
        print('  yt-dlp updated successfully.')
    else:
        subprocess.run(['python', '-m', 'pip', 'install', '-U', 'yt-dlp'],
                       capture_output=True)
        print('  yt-dlp update attempted via pip.')

def get_video_metadata(url):
    result = subprocess.run(
        ['yt-dlp', '--dump-json', '--no-playlist', url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f'  Failed to fetch metadata: {result.stderr}')
        sys.exit(1)
    return json.loads(result.stdout)

def download_audio(url, slug):
    dest   = os.path.join(AUDIO_FOLDER, f'{slug}.mp3')
    result = subprocess.run([
        'yt-dlp', '--no-playlist', '--extract-audio',
        '--audio-format', 'mp3', '--audio-quality', '0',
        '--format', 'bestaudio/best',
        '--postprocessor-args', 'ffmpeg:-q:a 0 -ac 2 -joint_stereo 1',
        '--output', dest, url
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f'  Download failed: {result.stderr}')
        sys.exit(1)
    if not os.path.isfile(dest):
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

    print(f'  Fetching video metadata...')
    meta      = get_video_metadata(url)
    title     = meta.get('title', 'Untitled')
    channel   = meta.get('channel', meta.get('uploader', 'Unknown Channel'))
    playlist  = meta.get('playlist_title')
    album     = playlist if playlist else channel
    thumbnail = meta.get('thumbnail')

    print(f'  Title:    {title}')
    print(f'  Channel:  {channel}')
    print(f'  Album:    {album}')

    art_path = None
    if thumbnail:
        img = fetch_and_resize_image(thumbnail)
        if img:
            art_path = os.path.join(TEMP_FOLDER, f'{slug}.jpg')
            img.save(art_path, 'JPEG', quality=90)
            print(f'  Art:      {art_path}')
        else:
            print(f'  Art:      failed to download thumbnail')

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