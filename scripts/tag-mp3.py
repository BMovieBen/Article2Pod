# tag-mp3.py
# Tags the single MP3 in C:\ComfyUI\output\audio with ID3 metadata,
# renames it to the slug, moves it to D:\Podcasts\[Album]\[Artist]\,
# then archives the txt file to C:\ComfyUI\temp\

import os, sys, json, shutil, glob
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, APIC, TRCK, ID3NoHeaderError

from utils import get_input_folder, get_audio_folder, get_temp_folder, get_podcasts_folder, get_track_log

AUDIO_FOLDER  = get_audio_folder()
TEMP_FOLDER   = get_temp_folder()
INPUT_FOLDER  = get_input_folder()
PODCASTS_FOLDER = get_podcasts_folder()
TRACK_LOG       = get_track_log()

def sanitize(name):
    return ''.join(c for c in name.strip() if c not in r'\/:*?"<>|')

def find_mp3():
    matches = glob.glob(os.path.join(AUDIO_FOLDER, '*.mp3'))
    return matches[0] if matches else None

def find_json():
    matches = glob.glob(os.path.join(TEMP_FOLDER, '*.json'))
    return matches[0] if matches else None

def load_track_log():
    if os.path.isfile(TRACK_LOG):
        with open(TRACK_LOG, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_track_log(log):
    with open(TRACK_LOG, 'w', encoding='utf-8') as f:
        json.dump(log, f, indent=2, ensure_ascii=False)

def get_next_track(meta):
    site   = meta.get('album',  'Unknown Site')
    author = meta.get('artist', 'Unknown Author')
    log    = load_track_log()
    key    = f'{site}|{author}'
    last   = log.get(key, 0)
    next_track = last + 1
    log[key]   = next_track
    save_track_log(log)
    return next_track

def tag_mp3(mp3_path, meta, art_path, track_number):
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()

    site   = meta.get('album',  '')   # website stored as album in JSON
    author = meta.get('artist', '')   # author stored as artist in JSON

    tags.add(TIT2(encoding=3, text=meta.get('title', '')))
    tags.add(TPE1(encoding=3, text=site))    # Artist = website
    tags.add(TPE2(encoding=3, text=site))    # Album Artist = website
    tags.add(TALB(encoding=3, text=author))  # Album = author
    tags.add(TRCK(encoding=3, text=str(track_number)))

    if art_path and os.path.isfile(art_path):
        with open(art_path, 'rb') as f:
            tags.add(APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,
                desc='Cover',
                data=f.read()
            ))

    tags.save(mp3_path)

def move_mp3(mp3_path, slug, meta):
    site   = meta.get('album',  'Unknown Site')
    author = meta.get('artist', 'Unknown Author')
    dest_folder = os.path.join(PODCASTS_FOLDER, sanitize(site), sanitize(author))
    os.makedirs(dest_folder, exist_ok=True)
    dest_path = os.path.join(dest_folder, f'{slug}.mp3')
    shutil.move(mp3_path, dest_path)
    return dest_path

def archive_txt(slug):
    txt_path = os.path.join(INPUT_FOLDER, f'{slug}.txt')
    if os.path.isfile(txt_path):
        shutil.move(txt_path, os.path.join(TEMP_FOLDER, f'{slug}.txt'))
        return txt_path
    return None

def main(slug):
    json_path = os.path.join(TEMP_FOLDER, f'{slug}.json')
    if not os.path.isfile(json_path):
        print(f'  No metadata JSON found for slug: {slug}')
        sys.exit(1)

    mp3_path = os.path.join(AUDIO_FOLDER, f'{slug}.mp3')
    if not os.path.isfile(mp3_path):
        print(f'  No MP3 found for slug: {slug}')
        sys.exit(1)

    with open(json_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)

    art_path     = os.path.join(TEMP_FOLDER, f'{slug}.jpg')
    track_number = get_next_track(meta)

    print(f'  File:       {slug}.mp3')
    print(f'  Title:      {meta.get("title")}')
    print(f'  Artist:     {meta.get("album")}')   # site = artist
    print(f'  Album:      {meta.get("artist")}')  # author = album
    print(f'  Art:        {art_path if os.path.isfile(art_path) else "none"}')
    print(f'  Track:      {track_number}')

    tag_mp3(mp3_path, meta, art_path, track_number)
    print(f'  Tags written.')

    dest = move_mp3(mp3_path, slug, meta)
    print(f'  Moved to:   {dest}')

    article_txt = os.path.join(INPUT_FOLDER, 'article.txt')
    if os.path.isfile(article_txt):
        os.remove(article_txt)

    print(f'\n  Done.')

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python tag-mp3.py <slug>')
        sys.exit(1)
    main(sys.argv[1])