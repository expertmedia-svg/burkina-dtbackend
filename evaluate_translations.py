"""Prepare human evaluation sheets; report ratings without inventing references."""
import argparse
import csv
import json
from pathlib import Path
from translation_engine import LANGUAGES

CASES = [
    ('salutation', 'Bonjour, comment allez-vous ?'),
    ('prix', 'Combien coûte ce sac de riz ?'),
    ('quantite', 'Je voudrais deux kilos de mil.'),
    ('negation', 'Je ne veux pas acheter ce produit.'),
    ('negation', 'Ne tournez pas à droite.'),
    ('temps', 'Hier, je suis allé au marché.'),
    ('temps', 'Demain, nous irons à Bobo-Dioulasso.'),
    ('conjugaison', 'Nous mangeons ensemble.'),
    ('nombre', 'Le prix est de 2 500 francs CFA.'),
    ('nombre', 'Le car part à 14 heures 30.'),
    ('nom_propre', 'Je cherche Aïssata à Ouagadougou.'),
    ('direction', 'Où se trouve la gare routière ?'),
    ('clarification', 'Pouvez-vous répéter plus lentement ?'),
    ('inconnu', 'Je souhaite recharger la batterie de mon téléphone.'),
    ('inconnu', 'Comment envoyer ce document par courrier électronique ?'),
    ('ambiguite', 'Je cherche un avocat.'),
    ('ambiguite', 'Il est devant la banque.'),
    ('politesse', 'Merci de votre aide.'),
    ('question', 'Avez-vous de l’eau potable ?'),
    ('accord', 'Les enfants sont arrivés.'),
    ('condition', 'Si le car est plein, je prendrai le suivant.'),
    ('opposition', 'Je veux le petit sac, pas le grand.'),
    ('possession', 'Ce sac appartient à ma sœur.'),
    ('demande', 'Appelez mon frère, s’il vous plaît.'),
]
FIELDS = ['id', 'language', 'category', 'french', 'reference_local', 'dialect', 'reviewer',
          'candidate_local', 'candidate_french', 'meaning_score_0_2', 'naturalness_score_0_2',
          'reverse_meaning_score_0_2', 'critical_error', 'notes']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'report'])
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        with args.file.open('x', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, FIELDS)
            writer.writeheader()
            for lang in LANGUAGES:
                for number, (category, french) in enumerate(CASES, 1):
                    writer.writerow({'id': f'{lang}-{number:02}', 'language': lang,
                                     'category': category, 'french': french})
    else:
        with args.file.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        for lang in LANGUAGES:
            selected = [r for r in rows if r['language'] == lang]
            reviewed = [r for r in selected if r['reviewer'].strip() and r['reference_local'].strip()
                        and r['meaning_score_0_2'] in ('0', '1', '2')]
            print(json.dumps({'language': lang, 'cases': len(selected), 'reviewed': len(reviewed),
                'meaning_average_0_2': sum(int(r['meaning_score_0_2']) for r in reviewed) / len(reviewed) if reviewed else None,
                'critical_errors': sum(r['critical_error'].lower() in ('oui', 'yes', '1') for r in reviewed),
                'status': 'évaluation humaine requise' if len(reviewed) < len(selected) else 'relue'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
