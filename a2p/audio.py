"""From finished segments to a tagged MP3 in the output folder.

Segments are joined as uncompressed audio, loudness-normalized, and
encoded to MP3 exactly once.
"""
import json, os, re, shutil, subprocess
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, APIC, TRCK, ID3NoHeaderError
from . import db, settings, textclean

TRIM = ('silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,areverse,'
        'silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,areverse')
RATE = '24000'

# Loudness: dynaudnorm lifts quiet passages, loudnorm (two-pass, linear) sets
# the level, and a limiter guards the peak. -3 dB true peak leaves headroom
# for the overshoot MP3 encoding adds.
DYNAUDNORM = 'dynaudnorm=f=500:g=15:p=0.75:m=8'
LOUDNORM   = 'I=-16:TP=-3.0:LRA=11'


def _ffmpeg(args, what):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-y'] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'ffmpeg could not {what}: {r.stderr.strip()[-600:]}')


def join(paths, gaps, end_pause, dest_wav, work_dir, trim=True):
    """Concatenate segment files into one WAV, with gaps[i] seconds of
    silence after segment i and end_pause at the end."""
    os.makedirs(work_dir, exist_ok=True)
    listing, made = [], []
    def silence(sec):
        p = os.path.join(work_dir, f'sil_{int(round(sec * 1000))}.wav')
        if not os.path.isfile(p):
            _ffmpeg(['-f', 'lavfi', '-i', f'anullsrc=r={RATE}:cl=mono', '-t', f'{sec:.3f}',
                     '-c:a', 'pcm_s16le', p], 'make silence')
            made.append(p)
        return p
    try:
        for i, (src, gap) in enumerate(zip(paths, gaps)):
            w = os.path.join(work_dir, f'join_{i:03d}.wav')
            _ffmpeg(['-i', src] + (['-af', TRIM] if trim else []) +
                    ['-ac', '1', '-ar', RATE, '-c:a', 'pcm_s16le', w], 'prepare a segment')
            made.append(w)
            listing.append(w)
            if gap and gap > 0:
                listing.append(silence(gap))
        if end_pause and end_pause > 0:
            listing.append(silence(end_pause))
        list_file = os.path.join(work_dir, 'concat.txt')
        with open(list_file, 'w', encoding='utf-8') as f:
            for p in listing:
                f.write("file '" + p.replace('\\', '/').replace("'", "'\\''") + "'\n")
        made.append(list_file)
        _ffmpeg(['-f', 'concat', '-safe', '0', '-i', list_file, '-c:a', 'pcm_s16le', dest_wav],
                'join the segments')
    finally:
        for p in made:
            try:
                os.remove(p)
            except OSError:
                pass


def encode(src, dest_mp3, normalize=True):
    """Encode to MP3, loudness-normalized when asked. If normalizing fails
    the audio is encoded as it is. Returns a note for the job log."""
    note = ''
    if normalize:
        measure = subprocess.run(
            ['ffmpeg', '-i', src, '-af', f'{DYNAUDNORM},loudnorm={LOUDNORM}:print_format=json',
             '-f', 'null', '-'], capture_output=True, text=True)
        m = re.search(r'\{[^{}]*"input_i"[^{}]*\}', measure.stderr, re.DOTALL)
        try:
            st = json.loads(m.group(0))
            chain = (f'{DYNAUDNORM},loudnorm={LOUDNORM}:measured_I={st["input_i"]}:'
                     f'measured_TP={st["input_tp"]}:measured_LRA={st["input_lra"]}:'
                     f'measured_thresh={st["input_thresh"]}:offset={st["target_offset"]}:linear=true,'
                     f'alimiter=limit=0.708:level=false:attack=5:release=50')
            _ffmpeg(['-i', src, '-af', chain, '-codec:a', 'libmp3lame', '-q:a', '2', dest_mp3],
                    'normalize')
            return 'normalized'
        except Exception as e:
            note = f'not normalized ({str(e)[:120]})'
    _ffmpeg(['-i', src, '-codec:a', 'libmp3lame', '-q:a', '2', dest_mp3], 'encode the MP3')
    return note or 'encoded'


def tag(mp3_path, title, site, author, track, art_path=None):
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=site))       # artist = site (how Plex groups them)
    tags.add(TPE2(encoding=3, text=site))
    tags.add(TALB(encoding=3, text=author))     # album = author
    tags.add(TRCK(encoding=3, text=str(track)))
    if art_path and os.path.isfile(art_path):
        with open(art_path, 'rb') as f:
            tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=f.read()))
    tags.save(mp3_path)


def publish(job, mp3_path):
    """Tag the MP3 and move it to output/<Site>/<Author>/<Site> - <Title>.mp3.
    Returns the final path."""
    title  = (job.get('title') or '').strip() or 'Untitled'
    site   = (job.get('site') or '').strip() or 'Unknown Site'
    author = (job.get('author') or '').strip() or 'Unknown Author'
    tag(mp3_path, title, site, author, db.next_track(site, author), job.get('art_path'))
    site_part  = textclean.sanitize_filename(site)
    safe_title = textclean.sanitize_filename(title)
    max_title  = max(10, 150 - len(site_part) - len(' - .mp3'))
    folder = os.path.join(settings.load()['output_dir'], site_part, textclean.sanitize_filename(author))
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f'{site_part} - {safe_title[:max_title].rstrip()}.mp3')
    shutil.move(mp3_path, dest)
    return dest


# ---- changing a finished file ---------------------------------------------
# The library is sorted by each file's modified time, so edits must not touch it.
def _save_keeping_time(tags, mp3_path):
    st = os.stat(mp3_path)
    tags.save(mp3_path)
    os.utime(mp3_path, ns=(st.st_atime_ns, st.st_mtime_ns))


def read_tags(mp3_path):
    """{'title', 'site', 'author', 'track', 'has_art'} from an MP3 we (or the
    original app) filed. Missing tags come back as ''."""
    try:
        tags = ID3(mp3_path)
    except Exception:
        return {'title': '', 'site': '', 'author': '', 'track': '', 'has_art': False}
    text = lambda key: str(tags[key].text[0]) if key in tags and tags[key].text else ''
    return {'title': text('TIT2'), 'site': text('TPE1'), 'author': text('TALB'),
            'track': text('TRCK'), 'has_art': bool(tags.getall('APIC'))}


def art_bytes(mp3_path):
    try:
        pics = ID3(mp3_path).getall('APIC')
        return pics[0].data if pics else None
    except Exception:
        return None


def set_art(mp3_path, jpeg_path):
    tags = ID3(mp3_path)
    tags.delall('APIC')
    with open(jpeg_path, 'rb') as f:
        tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=f.read()))
    _save_keeping_time(tags, mp3_path)


def retag(mp3_path, title, site, author):
    """Rewrite title/site/author and move the file to the folder and name
    that match (output/<Site>/<Author>/<Site> - <Title>.mp3). Returns the
    new path."""
    title  = (title or '').strip() or 'Untitled'
    site   = (site or '').strip() or 'Unknown Site'
    author = (author or '').strip() or 'Unknown Author'
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=site))
    tags.add(TPE2(encoding=3, text=site))
    tags.add(TALB(encoding=3, text=author))
    _save_keeping_time(tags, mp3_path)
    site_part = textclean.sanitize_filename(site)
    folder = os.path.join(settings.load()['output_dir'], site_part, textclean.sanitize_filename(author))
    os.makedirs(folder, exist_ok=True)
    safe_title = textclean.sanitize_filename(title)
    dest = os.path.join(folder, f'{site_part} - {safe_title[:max(10, 150 - len(site_part) - len(" - .mp3"))].rstrip()}.mp3')
    if os.path.normcase(os.path.abspath(dest)) != os.path.normcase(os.path.abspath(mp3_path)):
        old_dir = os.path.dirname(mp3_path)
        os.replace(mp3_path, dest)
        remove_empty_dirs(old_dir)
    return dest


def remove_empty_dirs(folder):
    """Remove a now-empty author folder and, if that empties it, the site folder."""
    root = os.path.normcase(os.path.abspath(settings.load()['output_dir']))
    for d in (folder, os.path.dirname(folder)):
        try:
            if os.path.normcase(os.path.abspath(d)) != root and not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass
