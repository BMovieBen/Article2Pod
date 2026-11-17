"""Live check (needs ComfyUI and the network): fetch articles and show what
text-model clean-up would remove, without changing anything.

    python tests/live_cleanup.py <url> [<url> ...]

Lines are shown shortened. "kept by rule" lists lines the model wanted
gone that the keep rules saved.
"""
import sys
sys.path.insert(0, '.')
from a2p import cleanup, settings, sources

MARKERS = ['Archives', 'dinosaur news', 'From ', 'back on Monday', 'real work of govern']

for url in sys.argv[1:]:
    item  = sources.fetch(url)
    lines = [l for l in item['text'].split('\n') if l.strip()]
    meta  = {'title': item['title'], 'author': item['author'], 'site': item['site'], 'url': url}
    print(f'\n=== {item["title"][:70]}  ({len(lines)} lines, {sum(len(l.split()) for l in lines)} words)')
    for m in MARKERS:
        hits = [l for l in lines if m in l]
        print(f'  extraction has "{m}": {len(hits)} line(s)')
    d = cleanup.decide(meta, lines, site=settings.site_entry(url, item['site'])) \
        if 'site' in cleanup.decide.__code__.co_varnames else cleanup.decide(meta, lines)
    print(f'  status {d["status"]} {d["note"]}  retried {d["retried"]}  {d["seconds"]}s  metadata {d["metadata"]}')
    print(f'  REMOVED {len(d["remove"])}:')
    for i in d['remove']:
        print(f'    [{i + 1}] ({len(lines[i].split())}w) {lines[i].strip()[:70]}')
    for i in d.get('kept_by_rule', []):
        print(f'    kept by rule [{i + 1}] {lines[i].strip()[:70]}')
