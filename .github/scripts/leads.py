#!/usr/bin/env python3
"""Sucht Firmen rund um Schorndorf (OpenStreetMap) und bewertet ihre Websites.
Ausgabe: JSON auf stdout. Läuft als GitHub Action (Cloud kommt nicht an OSM ran)."""
import json, re, sys, time, datetime, concurrent.futures as cf
import urllib.parse
import requests

CENTER = (48.805, 9.527)   # Schorndorf
RADIUS = 12000             # Meter
MIRRORS = ['https://overpass-api.de/api/interpreter',
           'https://overpass.private.coffee/api/interpreter',
           'https://overpass.openstreetmap.fr/api/interpreter']
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
      'Accept-Language': 'de-DE,de;q=0.9'}
AMENITY = {'restaurant','cafe','fast_food','bar','pub','biergarten','ice_cream','pharmacy','doctors','dentist','veterinary',
           'driving_school','car_wash','car_rental','clinic','nightclub','cinema'}
SKIP_SHOP = {'vacant','no','yes','kiosk','convenience','supermarket','department_store','fuel','lottery','ticket'}
SOCIAL = ('facebook.com','instagram.com','fb.com','linktr.ee','tiktok.com')
THIS_YEAR = datetime.date.today().year


def overpass():
    lat, lon = CENTER
    q = f"""[out:json][timeout:240];
(
  nwr(around:{RADIUS},{lat},{lon})[name][shop];
  nwr(around:{RADIUS},{lat},{lon})[name][craft];
  nwr(around:{RADIUS},{lat},{lon})[name][office];
  nwr(around:{RADIUS},{lat},{lon})[name][amenity];
  nwr(around:{RADIUS},{lat},{lon})[name][tourism~"^(hotel|guest_house|hostel|motel|apartment)$"];
  nwr(around:{RADIUS},{lat},{lon})[name][leisure~"^(fitness_centre|dance|sports_centre)$"];
  nwr(around:{RADIUS},{lat},{lon})[name][healthcare];
);
out center tags;"""
    for attempt in range(3):
        for m in MIRRORS:
            try:
                r = requests.post(m, data={'data': q}, headers=UA, timeout=300)
                if r.ok:
                    return r.json()['elements']
                print('overpass', m, r.status_code, file=sys.stderr)
            except Exception as e:
                print('overpass', m, e, file=sys.stderr)
        time.sleep(30)
    raise SystemExit('Overpass nicht erreichbar')


def keep(t):
    if t.get('brand') or t.get('brand:wikidata'):
        return False                          # Ketten (Lidl, dm, ...) brauchen mich nicht
    if t.get('amenity') and t['amenity'] not in AMENITY and not (t.get('shop') or t.get('craft') or t.get('office')):
        return False
    if t.get('shop') in SKIP_SHOP:
        return False
    if t.get('office') in ('government', 'association', 'religion', 'political_party', 'ngo', 'educational_institution'):
        return False
    return True


def web(t):
    return (t.get('website') or t.get('contact:website') or t.get('url') or '').strip()


def check(url):
    """Bewertet eine Website. Höhere Punktzahl = besserer Lead."""
    out = {'score': 0, 'why': []}
    if not url:
        out['score'] = 5; out['why'].append('keine Website'); return out
    u = url if re.match(r'^https?://', url, re.I) else 'http://' + url
    if any(s in u.lower() for s in SOCIAL):
        out['score'] = 5; out['why'].append('nur Social Media'); return out
    try:
        r = requests.get(u, headers=UA, timeout=15, allow_redirects=True)
        if 'charset' not in r.headers.get('content-type', '').lower():
            r.encoding = r.apparent_encoding
        html = r.text[:400000] if 'html' in r.headers.get('content-type', 'text/html') else ''
    except requests.exceptions.SSLError:
        out['score'] += 3; out['why'].append('SSL-Zertifikat kaputt'); return out
    except Exception:
        out['score'] = 4; out['why'].append('Website nicht erreichbar'); return out
    out['final'] = r.url
    if r.status_code >= 400:
        out['score'] = 4; out['why'].append(f'Website Fehler {r.status_code}'); return out
    low = html.lower()
    if any(s in r.url.lower() for s in SOCIAL):
        out['score'] = 5; out['why'].append('leitet auf Social Media um'); return out
    if not r.url.lower().startswith('https://'):
        out['score'] += 2; out['why'].append('kein https (Browser zeigt „nicht sicher“)')
    if not re.search(r'<meta[^>]+name=["\']?viewport', low):
        out['score'] += 3; out['why'].append('nicht fürs Handy gemacht')
    years = [int(y) for y in re.findall(r'(?:©|&copy;|copyright)[^0-9<]{0,20}(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})', low)]
    years = [y for y in years if 1995 <= y <= THIS_YEAR]
    if years:
        y = max(years); out['year'] = y
        if y <= THIS_YEAR - 7: out['score'] += 2; out['why'].append(f'Copyright {y}')
        elif y <= THIS_YEAR - 4: out['score'] += 1; out['why'].append(f'Copyright {y}')
    if re.search(r'<(frameset|marquee|font|center)\b', low) or low.count('<table') >= 4:
        out['score'] += 2; out['why'].append('sehr alter Seitenaufbau')
    if '.swf' in low or 'shockwave' in low:
        out['score'] += 2; out['why'].append('Flash')
    gen = re.search(r'<meta[^>]+name=["\']?generator["\']?[^>]+content=["\']([^"\']+)', low)
    if gen:
        g = gen.group(1); out['gen'] = g[:60]
        if re.search(r'frontpage|netobjects|webeasy|homepage baukasten|website x5|joomla! 1\.|wordpress [1-4]\.', g):
            out['score'] += 1; out['why'].append('alter Baukasten')
    if re.search(r'jquery[.-]?1\.[0-9]\b|jquery-1\.', low):
        out['score'] += 1; out['why'].append('veraltete Technik')
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
    if m: out['title'] = re.sub(r'\s+', ' ', m.group(1)).strip()[:90]
    return out


def main():
    els = overpass()
    firms, seen = [], set()
    for e in els:
        t = e.get('tags', {})
        if not keep(t):
            continue
        lat = e.get('lat') or (e.get('center') or {}).get('lat')
        lon = e.get('lon') or (e.get('center') or {}).get('lon')
        if lat is None:
            continue
        key = (t['name'].lower(), round(lat, 3), round(lon, 3))
        if key in seen:
            continue
        seen.add(key)
        firms.append({'id': e['type'][0] + str(e['id']), 'name': t['name'], 'lat': round(lat, 6), 'lon': round(lon, 6),
                      'cat': t.get('shop') or t.get('craft') or t.get('amenity') or t.get('office') or t.get('tourism')
                             or t.get('leisure') or t.get('healthcare'),
                      'addr': ', '.join(filter(None, [' '.join(filter(None, [t.get('addr:street'), t.get('addr:housenumber')])),
                                                      ' '.join(filter(None, [t.get('addr:postcode'), t.get('addr:city')]))])),
                      'phone': t.get('phone') or t.get('contact:phone') or '',
                      'email': t.get('email') or t.get('contact:email') or '',
                      'web': web(t), 'hours': t.get('opening_hours', '')})
    print(f'{len(firms)} Firmen, prüfe Websites ...', file=sys.stderr)
    with cf.ThreadPoolExecutor(16) as ex:
        for f, res in zip(firms, ex.map(lambda f: check(f['web']), firms)):
            f.update(res)
    firms.sort(key=lambda f: -f['score'])
    json.dump({'stand': datetime.datetime.utcnow().isoformat(timespec='minutes') + 'Z', 'center': CENTER,
               'radius_m': RADIUS, 'firmen': firms}, sys.stdout, ensure_ascii=False)


if __name__ == '__main__':
    main()
