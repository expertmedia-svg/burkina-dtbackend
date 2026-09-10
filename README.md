# Backend Burkina Langues — Groq

## Configuration

1. Copiez `.env.example` vers `.env`.
2. Créez une clé dans la console Groq et renseignez `GROQ_API_KEY`.
3. Laissez `GROQ_MODEL=openai/gpt-oss-120b` pour privilégier la qualité.
4. Démarrez avec `python server.py`.

La clé Groq reste uniquement sur le serveur. Les applications web et mobile
utilisent le proxy backend et ne doivent jamais embarquer la clé dans leur code.

Groq est le seul fournisseur IA : traduction, recherche de reformulations,
corrections et conversations passent par son API. Les anciens appels Gemini
et OpenAI, leurs réglages et leurs replis sont supprimés. La lecture utilise
le moteur vocal de l'appareil ; l'ancien service ElevenLabs est retiré.
Le tableau de bord serveur propose uniquement la clé Groq.
Le segment `/openai/v1` de l'URL **api.groq.com** désigne le protocole compatible
de Groq ; ce n'est pas un appel au service OpenAI. De même, le modèle
`openai/gpt-oss-120b` est exécuté et facturé chez Groq.

### Configuration pour le quota Qwen de 1 000 tokens de sortie/minute

```dotenv
GROQ_MODEL=qwen/qwen3.6-27b
GROQ_MAX_COMPLETION_TOKENS=512
```

`groq_transport.py` est obligatoire avec cette version de `server.py`.
Il identifie les requêtes avec `User-Agent: BurkinaDict/1.1`, conserve le mode
JSON et désactive le raisonnement Qwen (`reasoning_effort: none`) afin de
réserver le budget à la réponse. Ce réglage est documenté par Groq :
https://console.groq.com/docs/model/qwen/qwen3.6-27b

Pour ce modèle, l'appel IA préparatoire de reformulation est désactivé par
défaut. La recherche lexicale locale, le corpus et le Wiktionnaire restent
actifs. Une seule traduction est ainsi demandée à Groq. Le budget de 512
tokens est une limite par réponse, pas une garantie contre les quotas
partagés par plusieurs utilisateurs. Les phrases longues peuvent produire
une réponse tronquée ; elle est alors refusée et signalée dans le diagnostic.

Une erreur 429 temporaire est réessayée au plus une fois si `Retry-After`
indique huit secondes ou moins. Une requête annoncée trop grande, un quota
plus long ou une erreur JSON ne déclenchent pas de répétition automatique.
Les diagnostics sont conservés sans afficher les clés.

Le test réel exécute désormais une seule langue par lancement :

```bash
python3 smoke_translation.py --language moore
```

Tester les autres langues séparément avec `--language dioula` ou
`--language fulfulde`, après rétablissement du quota. Les paramètres et le
transport sont couverts par `test_groq_transport.py`.

Langues de traduction : français, mooré, dioula et fulfuldé. Seuls le mooré,
le dioula et le fulfuldé sont des langues cibles locales exposées par l'API.

## Voix

Le backend Groq fournit la compréhension et la traduction. La reconnaissance
mobile actuelle utilise le moteur vocal de l'appareil, puis Groq corrige les
approximations à partir du dictionnaire et des règles d'Académie. Le TTS Groq
actuel ne propose officiellement que l'anglais et l'arabe : il ne faut donc pas
le présenter comme une voix native mooré/dioula/fulfuldé/gourounsi. Pour une voix
locale précise, utilisez des enregistrements validés par des locuteurs ou un
modèle TTS entraîné sur ces langues.

## Traduction avec références et validation

Les routes `/api/v1/translate-word`, `/api/v1/translate-sentence` et l'ancienne
route `/api/v1/translate` utilisent maintenant le même moteur. Le français vers
une langue locale et le sens inverse sont pris en charge. Par exemple :

```json
{"text":"votre phrase locale","source_lang":"moore","target_lang":"fr"}
```

Le contrôle de permission porte sur la langue locale dans les deux sens.
Les couples langue locale → autre langue locale ne sont pas encore proposés.
La longueur maximale acceptée par le serveur est de 3 000 caractères.

Le moteur suit cet ordre :

1. Expression exacte dans le corpus relu, dans les deux sens. Les variantes
   contradictoires ne sont pas départagées par un simple premier résultat.
2. Entrée exacte du dictionnaire, avec son véritable statut de validation.
3. Recherche de vocabulaire pertinent : formes fléchies françaises, champs de
   sens, synonymes et exemples. Un appel Groq court propose des termes de
   recherche supplémentaires sans modifier le texte à traduire. Ces termes
   ne deviennent jamais des traductions de référence.
4. Consultation effective du Wiktionnaire français via l'API MediaWiki.
   Seuls les passages correspondant au français et à `mos`, `dyu` ou `ff`
   sont retenus. Le bambara n'est pas utilisé comme substitut du dioula.
   Chaque extrait garde le lien de sa révision et une indication de licence.
5. Traduction Groq avec les entrées pertinentes, les règles actives de la
   langue, des phrases relues comparables et les documents trouvés.
6. En cas d'échec, correspondance locale par expressions, avec les mots
   absents entre crochets. La négation n'est pas supprimée silencieusement.

Le Wiktionnaire est une source communautaire, pas une validation par un
locuteur burkinabè. Sa couverture et la pertinence dialectale sont variables.
Le moteur n'annonce pas qu'une recherche a réussi lorsqu'elle est vide ou en
panne. Une consultation regroupe au plus huit titres, avec un délai réseau
de six secondes et un cache mémoire borné. Le texte ou certains de ses termes
sont transmis à ce service public. Pour désactiver cette consultation :

```dotenv
EXTERNAL_RESEARCH_ENABLED=false
```

La recherche ne requiert aucune clé supplémentaire. La génération et la
reformulation des requêtes nécessitent toujours `GROQ_API_KEY`. Le nombre de
mots du dictionnaire n'est pas un plafond de génération, mais aucune sortie
IA n'est automatiquement considérée comme une traduction exacte.

### Contrat de résultat

- `validation_status` : `validated`, `pending_human_validation` ou `incomplete`.
- `warning` et `missing_terms` : avertissement et passages non traduits.
- `source` : dictionnaire, corpus relu, proposition IA ou repli local.
- `sources` : références réellement consultées ; les liens inventés par le
  modèle sont ignorés. Elles ne prouvent pas que chaque mot est correct.
- `research_status` : `available`, `no_match`, `unavailable` ou `disabled`.
- `confidence: null`, `confidence_type: not_calibrated` : aucun pourcentage
  de fiabilité artificiel n'est affiché.

Le mobile conserve ces informations et affiche un statut discret près du
résultat. Les sources sont consultables sous un volet repliable. Une phrase
IA n'est plus marquée mot par mot comme trouvée dans le dictionnaire.
La synthèse vocale existante reste générique ; cette intégration ne crée pas
de voix native et ne certifie pas les indications phonétiques du modèle.

### Relecture et corpus

Les propositions sont enregistrées dans `translation_proposals.json`, séparé
des dictionnaires. Elles ne sont pas réutilisées automatiquement. Les anciennes
entrées portant `source: ai_auto_enrichment` et non validées sont exclues du
moteur. Pour les ajouter à la file de relecture sans modifier les originaux :

```powershell
python review_translations.py migrate
python review_translations.py list
python review_translations.py approve --id IDENTIFIANT --reviewer "Nom du locuteur" --dialect "Variété relue" --translation "Traduction corrigée"
python review_translations.py reject --id IDENTIFIANT --reviewer "Nom du locuteur"
```

Exécuter ces commandes dans le dossier `backend` sur le serveur, après une
relecture réelle. Le nom renseigné est une trace de relecture, pas une
authentification d'expert. Une approbation crée une phrase dans
`linguistic_corpus.jsonl`, utilisable immédiatement sans redémarrer le serveur.
La relecture et les écritures du serveur partagent un verrou de fichier.
Ces fichiers de propositions et de corpus sont exclus du service de fichiers
public. Ils doivent être conservés lors des déploiements.

Il est aussi possible d'importer des phrases documentées dans ce format JSONL
(une ligne JSON par phrase). Les valeurs entre chevrons ci-dessous doivent
être remplacées par du contenu réellement relu ; ce n'est pas une traduction :

```json
{"id":"reference-001","language":"moore","french":"<phrase française>","local":"<phrase mooré relue>","validated":true,"reviewer":"<locuteur>","dialect":"<variété>","source":"<ouvrage ou collecte>","url":"<référence si disponible>"}
```

Le moteur exploite les exemples déjà présents dans les dictionnaires. Aucun
corpus prétendument validé n'est fabriqué au démarrage.

### Vérifications

Depuis la racine du projet :

```powershell
python -m unittest discover -s backend -p test_translation_engine.py -v
python -m unittest discover -s backend -p test_groq_only.py -v
python backend/smoke_translation.py
python backend/evaluate_translations.py report backend/evaluation_locuteurs.csv
```

Dans `burkina_dict` : `flutter test --no-pub`.

Le test réel utilise la clé Groq configurée sans l'afficher et conserve ses
propositions dans un dossier temporaire. Il ne certifie pas leur sens.
`evaluation_locuteurs.csv` contient 24 situations par langue, soit 72 cas :
négation, quantités, noms propres, ambiguïtés, temps et termes absents.
Un locuteur doit fournir les références, renseigner les traductions produites
dans les deux sens, puis noter le sens et le naturel de la formulation.
Les cellules de référence et les notes restent vides tant qu'elles n'ont pas
été réellement renseignées. Le rapport ne confond pas tests techniques et
évaluation linguistique.

Le déploiement nécessite de transférer `server.py`, `groq_transport.py` et
`translation_engine.py`, de conserver les dictionnaires/configurations,
de redémarrer le backend, puis de publier une nouvelle version mobile pour
les statuts et le sens inverse. Le code local ne met pas à jour Play Store.
# Base documentaire enrichie

Les références Wiktionnaire et SMOL sont intégrées à la traduction Groq.
Voir [ENRICHISSEMENT.md](ENRICHISSEMENT.md) pour les comptes, les limites et
l'installation, et [les crédits](reference_data/NOTICE.md) pour les licences.
Vérification hors ligne : `python3 check_reference_data.py`.
