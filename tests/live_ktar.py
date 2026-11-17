"""Live check (needs the network): classify KTAR pages with the source
plug-ins. Articles must stay articles; /watch/ pages must be JW Player
videos. (Before the cutover this also compared against the original app's
detector; all six matched.)

    python tests/live_ktar.py [url ...]
"""
import sys
sys.path.insert(0, '.')
from a2p import sources

URLS = [
    ('article', 'https://ktar.com/arizona-water-news/phoenix-wastewater-plant-2b-rehab-complete'),
    ('article', 'https://ktar.com/national-news/supreme-court-declines-to-hear-challenge-to-maryland-sensitive-places-gun-law'),
    ('jwplayer', 'https://ktar.com/watch/shorts/man-arrested-for-fatal-phoenix-care-facility-assault/01a1e5f8-2ba8-8008-c976-9e2f06cded75'),
    ('jwplayer', 'https://ktar.com/watch/kyrsten-sinema-warns-democrats-on-trump-accountability-vs-revenge/01a1d7cc-ab08-8008-fb72-121fa1cabcd7'),
    ('jwplayer', 'https://ktar.com/watch/faa-grounds-all-flights/01a1d76d-e550-8008-ea2e-597da82100f2'),
]
if len(sys.argv) > 1:
    URLS = [('?', u) for u in sys.argv[1:]]

for expect, url in URLS:
    try:
        item = sources.fetch(url)
        got = item['kind']
        extra = f'{len(item["text"].split())} words' if got == 'article' else item['download'].get('media_id', '')
        title = item['title']
    except sources.NeedsPaste as e:
        got, extra, title = 'needs paste', str(e), ''
    ok = 'OK ' if expect in ('?', got) else 'BAD'
    print(f'{ok} expected {expect:9} got {got:9} {extra:12} {title[:60]}')
