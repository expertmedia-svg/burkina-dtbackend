# Enrichissement documentaire du 10 septembre 2026

La base intégrée contient 9 466 références, en complément des dictionnaires existants :

| Langue | Entrées Wiktionnaire | Références SMOL via anglais | Total |
| --- | ---: | ---: | ---: |
| Mooré | 338 | 3 997 | 4 335 |
| Dioula | 445 | 3 992 | 4 437 |
| Fulfuldé / peul | 694 | 0 | 694 |

Il ne s'agit pas de 9 466 nouveaux mots français distincts ni de phrases validées.
Il peut y avoir des recoupements avec les dictionnaires historiques.

Le moteur charge les fichiers JSONL de reference_data/, recherche les références
par sens français ou forme locale et en transmet au maximum six à Groq avant la
recherche ponctuelle sur Internet. Les extraits sont limités en taille. Une panne
de la recherche en ligne ne retire plus les références déjà stockées sur le serveur.
Les mots-outils français sont exclus de cette recherche pour éviter, par exemple,
que « mon téléphone » fasse remonter « mon amour ».

Une correspondance Wiktionnaire simple et non ambiguë peut répondre sans appel IA
si le dictionnaire historique n'a pas d'entrée. Elle reste à vérifier par un locuteur.
Les correspondances SMOL via anglais servent uniquement de contexte : jamais de
réponse exacte automatique. Une entrée historique validée garde la priorité ; un
conflit avec une entrée historique non validée est envoyé à Groq avec les références.

Pour « Je voudrais recharger la batterie de mon téléphone. », la recherche locale
retrouve les concepts SMOL phone, battery et want en mooré et en dioula. Elle ne
fournit pas de preuve suffisante pour « recharger » au sens électrique. Ne remplacez
pas ce verbe par une traduction de charge au sens de prix ou d'accusation.
Pour cette phrase, aucune référence pertinente n'a été trouvée dans le lot peul.

## Installation sur le serveur existant

Copier les fichiers de l'archive d'enrichissement dans ~/apps/burkina-dtbackend/,
en conservant le sous-dossier reference_data/. L'archive ne contient pas de .env.
Elle contient les modules de traduction et leurs tests, pas les données utilisateur.
Depuis ce dossier :

```bash
python3 check_reference_data.py
python3 -m unittest test_translation_engine test_groq_only test_groq_transport test_reference_enrichment
pm2 restart burkina-backend --update-env
python3 smoke_translation.py --language moore
```

Le contrôle hors ligne doit afficher integrity: ok et total_references: 9466.
Le smoke doit afficher 4335 références mooré chargées. Le résultat Groq est à
examiner : ai_processed=true prouve un appel réussi, pas une bonne traduction.
Espacez les essais réels si Groq renvoie une limite de débit.
Le redémarrage est nécessaire pour recharger les références mises en cache.

## Mise à jour reproductible

```bash
python3 enrich_references.py
python3 enrich_smol.py
python3 check_reference_data.py
```

Le premier script récupère les catégories linguistiques du Wiktionnaire par son
API avec des révisions identifiables ; il ralentit après une réponse 429. Il ne
remplace les fichiers qu'une fois le téléchargement des trois langues terminé.
Le second utilise une révision précise de SMOL ; revoir la fiche de données et la
qualité avant de changer cette révision. Aucune clé IA n'est nécessaire pour importer.

Consulter reference_data/NOTICE.md pour les crédits, licences et transformations.
Les corpus MAFAND (licence non commerciale), MooreFRCollections (accès soumis à
conditions et sources hétérogènes), les PDF sans autorisation claire de reprise et
FLORES+ (corpus d'évaluation) n'ont pas été intégrés à cette base opérationnelle.

## Limite des vérifications

Les tests vérifient le chargement, les sources, les langues, les ambiguïtés, la
priorité aux entrées relues et la conservation des références sans accès réseau.
Ils ne mesurent pas l'exactitude linguistique. Un test réel avec la clé Groq du
serveur et une relecture de locuteur restent nécessaires après déploiement.
