"""Offline integrity and retrieval check; does not claim linguistic validation."""
import hashlib
import json
from pathlib import Path
from translation_engine import TranslationEngine, LANGUAGES


def check(directory=None):
    directory = Path(directory or Path(__file__).parent)
    folder = directory / 'reference_data'
    counts = {}
    for filename, prefix, count_field in [('manifest.json', '', 'entries'),
                                           ('manifest_smol.json', 'smol_', 'references')]:
        manifest = json.loads((folder / filename).read_text(encoding='utf-8'))
        for language, info in manifest['languages'].items():
            raw = (folder / (prefix + language + '.jsonl')).read_bytes()
            if hashlib.sha256(raw).hexdigest() != info['sha256']:
                raise ValueError('Checksum mismatch: ' + prefix + language)
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
            if len(rows) != info[count_field] or len({r['id'] for r in rows}) != len(rows):
                raise ValueError('Wrong count or duplicate IDs: ' + language)
            if not all(r['language'] == language and r['validated'] is False
                       and r['url'].startswith('https://') and r['license'] for r in rows):
                raise ValueError('Invalid provenance: ' + language)
            counts[prefix + language] = len(rows)
    engine = TranslationEngine({}, directory / 'linguistic_corpus.jsonl',
                               directory / 'unused_proposals.json', lambda *a, **k: None)
    result = {'integrity': 'ok', 'counts': counts, 'total_references': sum(counts.values()),
              'retrieval_for_phone_sentence': {}}
    for language in LANGUAGES:
        docs = engine.ranked_references('Je voudrais recharger la batterie de mon téléphone.', language, False)
        result['retrieval_for_phone_sentence'][language] = [{'id': d['id'], 'title': d['title']} for d in docs]
    return result


if __name__ == '__main__':
    print(json.dumps(check(), ensure_ascii=True, indent=2))
