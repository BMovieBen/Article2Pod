"""Getting the audio for download jobs (YouTube, JW Player, a page's own
MP3). Each returns the path of an MP3 in the job's work folder. Stop
cancels a download in progress."""
import os, subprocess, sys, time
import requests
from . import comfy, textclean

_ytdlp_updated = False


class Cancelled(Exception):
    pass


def _run(cmd, what):
    """Run a command, ending it early if Stop is pressed."""
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            encoding='utf-8', errors='replace')
    while proc.poll() is None:
        if comfy.stop.is_set():
            proc.kill()
            proc.wait()
            raise Cancelled()
        time.sleep(0.3)
    err = proc.stderr.read()
    if proc.returncode != 0:
        raise RuntimeError(f'{what} failed: {err.strip()[-600:]}')


def ytdlp(url, dest, log):
    global _ytdlp_updated
    if not _ytdlp_updated:                       # YouTube changes often; update once per app start
        _ytdlp_updated = True
        log('Checking for a yt-dlp update...')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'yt-dlp'],
                       capture_output=True, text=True)
    # No forced player client: yt-dlp's defaults work; the android client 403s.
    _run(['yt-dlp', '--no-playlist', '--extract-audio', '--audio-format', 'mp3', '--audio-quality', '0',
          '--postprocessor-args', 'ffmpeg:-q:a 0 -ac 2 -joint_stereo 1', '--output', dest, url], 'yt-dlp')


def jwplayer(media_id, dest, log):
    from .sources.jwplayer import stream_url
    src = stream_url(media_id)
    if not src:
        raise RuntimeError(f'No downloadable source for JW Player video {media_id}.')
    _run(['ffmpeg', '-y', '-loglevel', 'error', '-user_agent', textclean.CHROME_UA,
          '-i', src, '-vn', '-ac', '2', '-q:a', '0', dest], 'The video download')


def http(url, dest, log):
    with textclean.http_get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                if comfy.stop.is_set():
                    raise Cancelled()
                if chunk:
                    f.write(chunk)


def fetch(job, work_dir, log):
    """Download a job's audio. Returns the MP3 path. Raises Cancelled or
    RuntimeError."""
    how  = job['info'].get('download') or {}
    dest = os.path.join(work_dir, 'download.mp3')
    part = os.path.join(work_dir, 'download.part.mp3')
    for p in (dest, part):
        if os.path.isfile(p):
            os.remove(p)
    try:
        if how.get('how') == 'ytdlp':
            ytdlp(how['url'], dest, log)              # yt-dlp names its own temp files
        elif how.get('how') == 'jwplayer':
            jwplayer(how['media_id'], part, log)
        elif how.get('how') == 'http':
            http(how['url'], part, log)
        else:
            raise RuntimeError('This job has nothing to download.')
    except requests.RequestException as e:
        raise RuntimeError(f'The download failed: {e}')
    finally:
        if comfy.stop.is_set() and os.path.isfile(part):
            os.remove(part)
    if os.path.isfile(part):
        os.replace(part, dest)
    if not os.path.isfile(dest):
        raise RuntimeError('The download finished but no MP3 was produced.')
    return dest
