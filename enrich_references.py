"""Import versioned, language-isolated Wiktionary references (no AI generation).

Run manually to refresh the distributable reference snapshot. Original dictionaries
and the human-reviewed corpus are never modified.
"""
import argparse
import datetime
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

LANGUAGES = {'moore': ('mos', 'moré'), 'dioula': ('dyu', 'dioula'),
             'fulfulde': ('ff', 'peul')}
LICENSE = 'CC BY-SA 4.0'
LICENSE_URL = 'https://creativecommons.org/licenses/by-sa/4.0/'


def extract(page, language):
    revisions = page.get('revisions', [])
    if not revisions:
        return None
    revision = revisions[0]
    raw = revision['slots']['main']['content']
    code = LANGUAGES[language][0]
    sections = re.split(r'(?m)^==\s*(?![=])', raw)
    section = next((s for s in sections if re.match(
        r'\{\{langue\|\s*' + re.escape(code) + r'\s*\}\}', s.strip())), '')
    definitions = re.findall(r'(?m)^#+(?![*:])\s*(.+)$', section)
    if not definitions:
        return None
    # Only simple, literal glosses are eligible for exact lookup. Definitions
    # containing templates/qualifiers remain context, never guessed equivalents.
    glosses = []
    for definition in definitions:
        plain = re.sub(r'\[\[(?:[^]|]+\|)?([^]]+)\]\]', r'\1', definition)
        plain = plain.replace("'''", '').replace("''", '').strip().rstrip('.')
        if (plain and len(plain.split()) <= 5 and
                not re.search(r'[{}\[\]()<>=:;/*]|https?://', plain)):
            glosses.append(plain)
    return {'id': 'wiki:' + str(revision['revid']), 'language': language,
            'local': page['title'], 'glosses': glosses,
            'definitions': definitions, 'excerpt': section.strip(),
            'title': 'Wiktionnaire — ' + page['title'],
            'url': 'https://fr.wiktionary.org/w/index.php?oldid=' + str(revision['revid']),
            'history_url': 'https://fr.wiktionary.org/w/index.php?' +
                urllib.parse.urlencode({'title': page['title'], 'action': 'history'}),
            'license': LICENSE, 'license_url': LICENSE_URL,
            'attribution': 'Contributeurs du Wiktionnaire', 'validated': False,
            'dialect': 'Non précisé par cette importation ; usage burkinabè à vérifier',
            'transformation': 'Section linguistique isolée ; gloses simples extraites sans génération.'}


def download(language):
    params = {'action': 'query', 'format': 'json', 'formatversion': 2,
              'generator': 'categorymembers', 'gcmtitle': 'Catégorie:' + LANGUAGES[language][1],
              'gcmnamespace': 0, 'gcmlimit': 50, 'prop': 'revisions',
              'rvprop': 'ids|content', 'rvslots': 'main', 'maxlag': 5}
    rows = {}
    while True:
        request = urllib.request.Request('https://fr.wiktionary.org/w/api.php?' +
            urllib.parse.urlencode(params), headers={'User-Agent': 'BurkinaDict/1.1 (reference importer)',
                                                    'Accept': 'application/json'})
        for attempt in range(6):
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    data = json.load(response)
                break
            except urllib.error.HTTPError as error:
                if error.code not in (429, 502, 503, 504) or attempt == 5:
                    raise
                try:
                    delay = float(error.headers.get('Retry-After', 10 * (attempt + 1)))
                except ValueError:
                    delay = 30
                if delay > 60:
                    raise
                print(language + ': pause serveur ' + str(delay) + ' s', flush=True)
                time.sleep(max(1, delay))
        if 'error' in data or data.get('warnings'):
            raise RuntimeError('Wiktionary returned an error or truncated query: ' + str(data.get('error', data.get('warnings'))))
        for page in data.get('query', {}).get('pages', []):
            row = extract(page, language)
            if row:
                rows[row['id']] = row
        print(language + ': ' + str(len(rows)) + ' références', flush=True)
        if 'continue' not in data:
            break
        params.update(data['continue'])
        time.sleep(2)
    if not rows:
        raise RuntimeError('Empty import for ' + language)
    return sorted(rows.values(), key=lambda row: row['local'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'reference_data')
    args = parser.parse_args()
    # Download all languages before replacing any existing snapshot.
    snapshots = {language: download(language) for language in LANGUAGES}
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {'retrieved_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'source': 'https://fr.wiktionary.org/', 'license': LICENSE,
                'license_url': LICENSE_URL, 'languages': {}}
    for language, rows in snapshots.items():
        payload = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode('utf-8')
        path = args.output / (language + '.jsonl')
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(payload)
        temporary.replace(path)
        manifest['languages'][language] = {'entries': len(rows),
            'simple_glosses': sum(len(row['glosses']) for row in rows),
            'sha256': hashlib.sha256(payload).hexdigest()}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
