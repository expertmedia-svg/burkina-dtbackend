"""Local human review workflow. Run on the server, never from model output.

Approval adds a reviewed bilingual sentence to linguistic_corpus.jsonl.
The server reads that corpus on each translation; no restart is required.
"""
import argparse
import json
import time
from pathlib import Path
from translation_engine import read_json, write_json, proposal_lock


def review(directory, identifier, decision, reviewer, corrected=None, dialect=None):
    directory = Path(directory)
    if not reviewer.strip():
        raise ValueError('Le nom du locuteur relecteur est obligatoire.')
    path = directory / 'translation_proposals.json'
    with proposal_lock(path):
        proposals = read_json(path, {})
        if identifier not in proposals:
            raise ValueError('Proposition inconnue.')
        proposal = proposals[identifier]
        if proposal['status'] != 'pending_human_validation':
            raise ValueError('Cette proposition a déjà été traitée.')
        translation = (corrected if corrected is not None else proposal['translation']).strip()
        if decision == 'approve':
            if not translation or '[' in translation or ']' in translation:
                raise ValueError('Une traduction complète est requise avant validation.')
            if not dialect or not dialect.strip():
                raise ValueError('Précisez la variété linguistique relue avec --dialect.')
            reverse = proposal['target_lang'] == 'fr'
            row = {'id': identifier, 'language': proposal['source_lang'] if reverse else proposal['target_lang'],
                   'french': translation if reverse else proposal['text'],
                   'local': proposal['text'] if reverse else translation,
                   'validated': True, 'reviewer': reviewer.strip(), 'dialect': dialect.strip(),
                   'reviewed_at': time.time(), 'source': 'Validation humaine : ' + reviewer.strip(),
                   'references': proposal.get('sources', [])}
            corpus = directory / 'linguistic_corpus.jsonl'
            # Idempotent append if a prior run stopped before saving proposal status.
            rows = [json.loads(line) for line in corpus.read_text(encoding='utf-8').splitlines() if line.strip()] if corpus.exists() else []
            if not any(record.get('id') == identifier for record in rows):
                rows.append(row)
                temp = corpus.with_suffix('.jsonl.tmp')
                temp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
                temp.replace(corpus)
        proposal.update(status='validated' if decision == 'approve' else 'rejected',
                        reviewer=reviewer.strip(), reviewed_at=time.time())
        write_json(path, proposals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'approve', 'reject', 'migrate'])
    parser.add_argument('--id')
    parser.add_argument('--reviewer', default='')
    parser.add_argument('--translation')
    parser.add_argument('--dialect')
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    if args.action == 'migrate':
        # Read-only migration of historical AI entries into the review queue.
        # Originals remain preserved and are filtered out by the engine.
        from translation_engine import ProposalStore, LANGUAGES, usable
        store = ProposalStore(args.directory / 'translation_proposals.json')
        for lang in LANGUAGES:
            name = 'dictionnaire_' + lang + '_1000.csv'
            path = args.directory.parent / name
            if not path.exists():
                path = args.directory / name
            for french, entry in read_json(path, {}).items():
                if not usable(entry):
                    store.add('fr', lang, french, entry['translation'], [])
        print('Les anciennes propositions IA sont disponibles pour relecture.')
    elif args.action == 'list':
        for row in read_json(args.directory / 'translation_proposals.json', {}).values():
            if row['status'] == 'pending_human_validation':
                print(json.dumps(row, ensure_ascii=False))
    else:
        try:
            review(args.directory, args.id, args.action, args.reviewer, args.translation, args.dialect)
        except ValueError as error:
            parser.error(str(error))
        print('Relecture enregistrée.')


if __name__ == '__main__':
    main()
