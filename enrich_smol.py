"""Import SMOL/GATITOS lexical references, preserving the English pivot.

These are NOT directly translated French/local pairs. Ambiguous English senses
must be resolved from context; these rows never qualify for exact translation.
"""
import argparse
import datetime
import hashlib
import json
import urllib.request
from pathlib import Path

REVISION = 'fdaff3a1a019f89fa30a562a85d7c1d3e9150444'
REPO = 'https://huggingface.co/datasets/google/smol'


def load(code, cache=None):
    filename = 'en_' + code + '.jsonl'
    path = cache / filename if cache else None
    url = REPO + '/resolve/' + REVISION + '/gatitos/' + filename
    raw = path.read_bytes() if path and path.exists() else urllib.request.urlopen(url, timeout=45).read()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    for row in rows:
        if (row.get('sl') != 'en' or row.get('tl') != code or
                not isinstance(row.get('src'), str) or not isinstance(row.get('trgs'), list) or
                not row['trgs'] or not all(isinstance(s, str) and s.strip() for s in row['trgs'])):
            raise ValueError('Unexpected SMOL row for ' + code)
    return rows, {'url': url, 'sha256': hashlib.sha256(raw).hexdigest()}


def align(french, local, language, code):
    by_english = {}
    for row in french:
        by_english.setdefault(row['src'], []).append(row)
    output = {}
    for row in local:
        matches = by_english.get(row['src'], [])
        if not matches:
            continue
        french_terms = sorted({s for match in matches for s in match['trgs']})
        identity = json.dumps([language, row, matches], sort_keys=True, ensure_ascii=False)
        identifier = 'smol:' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        url = REPO + '/blob/' + REVISION + '/gatitos/en_' + code + '.jsonl'
        excerpt = ('Repères français : ' + ' ; '.join(french_terms) +
                   '\nPivot anglais : ' + row['src'] + '\nFormes locales : ' + ' ; '.join(row['trgs']) +
                   '\nATTENTION : deux correspondances avec l’anglais, pas une traduction directe '
                   'français/local validée. Le mot anglais peut avoir des sens différents. '
                   'Ne pas assimiler une charge électrique, un prix et une accusation. '
                   'Respecter les variantes et vérifier le sens dans la phrase.')
        output[identifier] = {'id': identifier, 'language': language, 'kind': 'pivot_lexicon',
            'local': ' ; '.join(row['trgs']), 'glosses': [], 'definitions': french_terms,
            'pivot_en': row['src'], 'french_hints': french_terms, 'local_forms': row['trgs'],
            'excerpt': excerpt, 'source_rows': {'french': matches, 'local': row},
            'title': 'Google SMOL/GATITOS — ' + row['src'] + ' (via anglais)',
            'url': url, 'french_source_url': REPO + '/blob/' + REVISION + '/gatitos/en_fr.jsonl',
            'license': 'CC BY 4.0', 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
            'attribution': 'Google et contributeurs SMOL/GATITOS ; voir la fiche du jeu de données',
            'validated': False, 'dialect': 'Usage burkinabè à vérifier',
            'transformation': 'Rapprochement exact sur le texte anglais ; aucune traduction générée.'}
    return sorted(output.values(), key=lambda r: r['id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'reference_data')
    args = parser.parse_args()
    french, provenance = load('fr', args.cache)
    manifest = {'source': REPO, 'revision': REVISION, 'license': 'CC BY 4.0',
                'retrieved_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'inputs': {'fr': provenance}, 'languages': {},
                'excluded': 'Peul SMOL : réserves explicites de qualité et de variété dans la fiche source.'}
    snapshots = {}
    for language, code in [('moore', 'mos'), ('dioula', 'dyu')]:
        rows, provenance = load(code, args.cache)
        manifest['inputs'][code] = provenance
        snapshots[language] = align(french, rows, language, code)
    args.output.mkdir(parents=True, exist_ok=True)
    for language, rows in snapshots.items():
        payload = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode('utf-8')
        path = args.output / ('smol_' + language + '.jsonl')
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(payload)
        temporary.replace(path)
        manifest['languages'][language] = {'references': len(rows),
            'sha256': hashlib.sha256(payload).hexdigest()}
    (args.output / 'manifest_smol.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
