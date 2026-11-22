#!/usr/bin/env python3
"""Holt frei lizenzierte Fotos von Wikimedia Commons (für Kunden-Entwürfe).
Liest .github/bilder-anfrage.json: {"ordner": "...", "suchen": ["...", ...], "pro_suche": 6}"""
import json, os, re, sys, requests
UA = {'User-Agent': 'VisibleDigitalBilder/1.0 (website drafts)'}
OK = re.compile(r'^(cc0|cc[- ]by(-sa)?[- ]?[0-9.]*|public domain|pd.*)', re.I)
a = json.load(open('.github/bilder-anfrage.json'))
out = os.path.join('/tmp/bilder', a['ordner']); os.makedirs(out, exist_ok=True)
meta, seen = [], set()
for q in a['suchen']:
    r = requests.get('https://commons.wikimedia.org/w/api.php', headers=UA, timeout=60, params={
        'action': 'query', 'format': 'json', 'generator': 'search', 'gsrnamespace': 6, 'gsrlimit': 30, 'gsrsearch': q + ' filetype:bitmap',
        'prop': 'imageinfo', 'iiprop': 'url|size|extmetadata', 'iiurlwidth': 1600})
    pages = sorted((r.json().get('query') or {}).get('pages', {}).values(), key=lambda p: p.get('index', 99))
    n = 0
    for p in pages:
        ii = (p.get('imageinfo') or [{}])[0]; em = ii.get('extmetadata', {})
        lic = (em.get('LicenseShortName') or {}).get('value', '')
        if not OK.match(lic) or ii.get('width', 0) < 1200 or p['title'] in seen: continue
        seen.add(p['title'])
        name = re.sub(r'[^a-z0-9]+', '-', q.lower()).strip('-') + f'-{n+1}.jpg'
        try:
            img = requests.get(ii['thumburl'], headers=UA, timeout=60); img.raise_for_status()
        except Exception as e:
            print(e, file=sys.stderr); continue
        open(os.path.join(out, name), 'wb').write(img.content)
        artist = re.sub('<[^>]+>', '', (em.get('Artist') or {}).get('value', '')).strip()
        meta.append({'datei': name, 'suche': q, 'titel': p['title'], 'seite': ii.get('descriptionurl'), 'autor': artist, 'lizenz': lic,
                     'beschreibung': re.sub('<[^>]+>', '', (em.get('ImageDescription') or {}).get('value', ''))[:200]})
        n += 1
        if n >= a.get('pro_suche', 6): break
json.dump(meta, open(os.path.join(out, 'quellen.json'), 'w'), ensure_ascii=False, indent=1)
print(len(meta), 'Bilder')
