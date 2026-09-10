# Burkina Dict Experts — installation et utilisation

Application Android indépendante : `com.burkinafaso.burkina_dict.experts`.
Elle s'installe à côté de Burkina Langues. Elle utilise le même serveur HTTPS,
avec ses propres comptes experts. Aucune clé Groq ni aucun mot de passe n'est
embarqué dans l'APK. La clé Groq reste dans le `.env` du backend.

## Fonctions livrées

- Connexion avec compte individuel, rôle contributeur ou relecteur et langues attribuées.
- Consultation, recherche et correction des entrées du dictionnaire.
- Ajout et correction de traductions complètes et de formes orthographiques.
- Écoute des segments WAV : pause, déplacement, ralenti et répétition.
- Champs distincts pour la transcription locale, la traduction française,
  le locuteur, la variété linguistique et la référence d'autorisation pour la voix.
- Documents Word `.docx` consultables comme références, avec paragraphes et tableaux
  extraits dans l'ordre du document. Un ancien `.doc` doit être enregistré en `.docx`.
- Brouillons sur le téléphone et le serveur, soumission, validation ou retour en correction.
- Historique des modifications dans SQLite et contrôle de version contre l'écrasement
  des corrections d'un autre expert. Une session dure 12 heures et n'est pas conservée
  lors d'un redémarrage complet de l'application.
- Corpus exportable pour traduction, reconnaissance vocale et synthèse vocale.

Les validations alimentent immédiatement les traductions du backend via
`expert_data/expert_validated.json`. Les entrées approuvées du dictionnaire ont la
priorité ; les phrases relues rejoignent le corpus de référence. Les corrections
orthographiques s'appliquent aux saisies complètes correspondantes, sans remplacement
aveugle dans les autres mots. Une contribution validée remise en brouillon est retirée
de cette publication jusqu'à sa nouvelle validation.

## Première installation sur le serveur existant

Copier le contenu de `burkina_experts_backend.zip` dans `~/apps/burkina-dtbackend/`.
Cette archive ne contient ni `.env`, ni comptes, ni fichiers utilisateur. Conserver
les sous-dossiers. Installer l'APK sur les téléphones des experts.

Dans le terminal du serveur :

```bash
cd ~/apps/burkina-dtbackend
python3 -m unittest discover -p 'test_*.py'
python3 expert_api.py create-user responsable --role reviewer --languages moore dioula fulfulde
python3 expert_api.py create-user expert_moore --role expert --languages moore
python3 expert_api.py create-user expert_dioula --role expert --languages dioula
python3 expert_api.py import-dictionary .
pm2 restart burkina-backend --update-env
```

Chaque commande de création demande le mot de passe dans le terminal sans l'afficher.
Utiliser au moins 12 caractères. Réexécuter la commande pour changer un mot de passe,
un rôle ou les langues ; les sessions précédentes du compte sont alors révoquées.
Ne pas copier le même compte sur tous les téléphones si l'on veut identifier les auteurs.

Dans l'APK : serveur `https://burkinadt.yingr-ai.com`, puis l'identifiant et le mot de
passe du compte. `/expert-api/` est servi par le backend existant ; il n'y a pas de
second port public à ouvrir. Si le proxy est configuré par liste de routes, transmettre
aussi ce préfixe au backend. L'audio utilise des requêtes HTTP Range authentifiées.

Pour exposer les propositions IA déjà en attente aux experts :

```bash
python3 expert_api.py import-proposals translation_proposals.json
```

Ces imports sont idempotents et n'écrasent pas le travail déjà effectué dans l'atelier.
Les décisions de l'atelier restent dans sa base ; le fichier historique des propositions
IA est conservé sans modification.

## Les segments déjà disponibles

L'archive `burkina_experts_audio_initial.zip` contient le sous-dossier `local_tts/`,
avec les manifestes et les 368 WAV existants (249 mooré, 119 dioula, environ 23 minutes).
La décompresser dans le dossier du backend puis exécuter :

```bash
python3 expert_api.py import-audio local_tts
```

Le responsable voit ensuite ces segments dans l'onglet audio. Les trois textes
historiques déjà remplis ne sont pas présumés être en mooré : ils sont conservés
dans « Ancien texte à vérifier ». Les CSV originaux ne sont pas modifiés.
Cette archive ne contient pas les 20 heures du disque externe.

## Importer les enregistrements et Word du disque externe plus tard

1. Préparer les segments dans le projet `local_tts` existant, en conservant les originaux.
   Le format courant est un WAV par segment, avec son chemin et ses temps dans
   `data/manifests/moore_segments.csv` ou `dioula_segments.csv`.
2. Si les textes sont déjà alignés, ajouter `text_local`, `text_fr`, `speaker_id`,
   `dialect` et `consent_ref` au CSV. Sans alignement, laisser les textes vides.
3. Copier ce dossier de segments sur le serveur et exécuter `import-audio` comme ci-dessus.
   Utiliser des identifiants de segment uniques pour chaque nouveau lot : les identifiants
   déjà présents sont ignorés afin de protéger les corrections des experts.
4. Importer les `.docx` en indiquant à quel enregistrement ils correspondent, par exemple :

```bash
python3 expert_api.py import-word '/chemin/document_moore.docx' --language moore --source-file 'enregistrement01.mpeg'
```

L'icône document dans la fiche d'un segment ouvre les textes de référence de sa langue.
L'expert écoute chaque segment puis copie/corrige uniquement le passage correspondant.
Un document Word associé à un enregistrement entier n'est pas automatiquement aligné
sur chaque découpe : aucun alignement artificiel n'est présenté comme validé.

## Export pour l'entraînement

```bash
python3 expert_api.py export expert_exports/lot_001
```

Le dossier produit contient :

- `translation.jsonl` : paires français/langue locale approuvées, variété et relecteur ;
- `speech.jsonl` : audio, transcription locale exacte, français, locuteur et partition ;
- `audio/` : copies des segments retenus pour la voix ;
- `report.json` : quantités, heures et segments exclus de l'export vocal.

L'export vocal exige un locuteur identifié et une référence d'autorisation d'utilisation.
Les partitions sont attribuées de façon stable par locuteur pour les audios : un même
identifiant n'apparaît pas dans plusieurs partitions. Les identifiants doivent désigner
la même personne dans tous les lots, sans variantes de frappe. Avec peu de locuteurs,
validation ou test peuvent rester vides : le rapport le signale par leurs comptes.
Les annotations et les partitions doivent être inspectées avant l'entraînement.

L'application prépare les données et améliore la base de référence de Burkina Dict.
Elle ne réentraîne pas les paramètres d'un modèle Groq. L'entraînement d'un modèle
de voix ou de traduction sera une étape distincte, après importation et relecture des
20 heures, choix du modèle cible et disponibilité du matériel de calcul.

## Sauvegarde et mise à jour

Les comptes, sessions, travaux et audit sont dans `expert_data/experts.sqlite3`.
Les WAV sont dans `expert_data/audio/`, les documents Word importés dans la base.
Sauvegarder le dossier `expert_data` lorsque le backend est arrêté, ou effectuer une
sauvegarde SQLite cohérente et copier les WAV. Ne jamais remplacer ce dossier par une
base de démonstration lors d'une mise à jour. Le chemin peut être changé avec
`EXPERT_DATA_DIR` (même valeur pour le backend et les commandes d'administration).

Les brouillons locaux peuvent contenir des textes de travail et sont conservés dans
le stockage privé de l'application. La désinstallation les efface. En cas de conflit,
la version serveur n'est pas écrasée ; le brouillon local reste sur le téléphone.

## Construction de l'APK

Depuis le projet `burkina_dict` :

```bash
flutter build apk --release --target lib/expert_main.dart --dart-define=EXPERT_APP=true --build-name=1.0.0 --build-number=1
```

Ne pas omettre `EXPERT_APP=true` : ce paramètre choisit l'identifiant Android séparé
et le nom « Burkina Dict Experts ». Le point d'entrée habituel reste `lib/main.dart`.
Pour les mises à jour Experts, incrémenter le numéro de version et conserver la clé
de signature Android utilisée pour la première installation.

Vérification locale : tests Python d'accès, validation, export, import Word et HTTP
Range ; tests Flutter des écrans de connexion et de filtrage ; analyse statique Dart.
La connexion au serveur de production et l'écoute sur téléphone nécessitent son
déploiement ; la réussite de la compilation ne remplace pas cet essai.
