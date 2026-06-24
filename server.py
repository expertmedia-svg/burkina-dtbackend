#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Burkina Dict Advanced Backend Server
- Serves admin dashboard and user portal
- Loads API Keys from environment or .env
- Manages accounts (users.json)
- Manages client keys and pending requests (client_keys.json)
- Translation engine with AI fallback and local dictionaries
"""

import http.server
import json
import os
import re
import urllib.request
import urllib.error
import hashlib
from datetime import datetime

PORT = 8000
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BACKEND_DIR)

# File paths
CONFIG_PATH = os.path.join(BACKEND_DIR, 'academy_config.json')
CLIENT_KEYS_PATH = os.path.join(BACKEND_DIR, 'client_keys.json')
USERS_PATH = os.path.join(BACKEND_DIR, 'users.json')
ENV_PATH = os.path.join(BACKEND_DIR, '.env')

# Map local languages to dictionary files (located in the root folder)
DICTIONARY_FILES = {
    'moore': 'dictionnaire_moore_1000.csv',
    'dioula': 'dictionnaire_dioula_1000.csv',
    'fulfulde': 'dictionnaire_fulfulde_1000.csv',
    'gourounsi': 'dictionnaire_gourounsi_500.csv',
    'bissa': 'dictionnaire_bissa_500.csv'
}

# Parse .env if exists
def load_env():
    if os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        parts = line.split('=', 1)
                        key = parts[0].strip()
                        val = parts[1].strip().strip('"').strip("'")
                        os.environ[key] = val
            print("Loaded environment variables from .env")
        except Exception as e:
            print("Error parsing .env file:", e)

load_env()

def structure_word(french_word, local_translation, current_entry=None):
    if isinstance(current_entry, dict):
        defaults = {
            "translation": local_translation,
            "category": "Inconnu",
            "senses": f"Traduction de {french_word}",
            "example_fr": "",
            "example_local": "",
            "dialect": "Standard",
            "confidence": 1.0 if current_entry.get("validated", False) else 0.8,
            "validated": False,
            "syllables": "",
            "phonetic": "",
            "vocal_writing": local_translation,
            "reading_rhythm": "normal",
            "tone_accent": "",
            "audio_remark": ""
        }
        for k, v in defaults.items():
            if k not in current_entry:
                current_entry[k] = v
        return current_entry
    else:
        cat = "Inconnu"
        fw = french_word.lower()
        if fw.endswith("er") or fw.endswith("ir") or fw.endswith("dre"):
            cat = "Verbe"
        elif fw in ["je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "moi", "toi", "lui", "eux"]:
            cat = "Pronom"
        elif fw in ["un", "une", "des", "le", "la", "les"]:
            cat = "Déterminant"
        elif fw in ["et", "ou", "mais", "donc", "car", "ni", "or"]:
            cat = "Conjonction"
            
        syllables = ""
        phonetic = ""
        vocal_writing = local_translation
        audio_remark = ""
        if fw == "bonjour":
            if local_translation.lower() == "ne y yibeoogo":
                syllables = "Ne / y / yi / beo / go"
                phonetic = "Nè y yi-bé-o-go"
                vocal_writing = "Nè y yi-bé-o-go"
                audio_remark = "lire doucement, ton chaleureux"
        elif fw == "bonsoir":
            if local_translation.lower() == "niyungo":
                syllables = "Ni / yun / go"
                phonetic = "Ni-yun-go"
                vocal_writing = "Ni-youn-go"
                audio_remark = "lire doucement"
                
        return {
            "translation": local_translation,
            "category": cat,
            "senses": f"Traduction standard de {french_word}",
            "example_fr": "",
            "example_local": "",
            "dialect": "Standard",
            "confidence": 0.8,
            "validated": False,
            "syllables": syllables,
            "phonetic": phonetic,
            "vocal_writing": vocal_writing,
            "reading_rhythm": "normal",
            "tone_accent": "",
            "audio_remark": audio_remark
        }

# Load and migrate dictionaries
dictionaries = {}
for lang, filename in DICTIONARY_FILES.items():
    file_path = os.path.join(ROOT_DIR, filename)
    if not os.path.exists(file_path):
        file_path = os.path.join(BACKEND_DIR, filename)
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                raw_dict = json.load(f)
            
            migrated = False
            structured_dict = {}
            for key, val in raw_dict.items():
                if isinstance(val, str):
                    structured_dict[key] = structure_word(key, val)
                    migrated = True
                else:
                    structured_dict[key] = structure_word(key, val.get("translation", ""), val)
            
            dictionaries[lang] = structured_dict
            if migrated:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(structured_dict, f, indent=2, ensure_ascii=False)
                print(f"Migrated and saved dictionary {lang} to structured format.")
            print(f"Loaded dictionary {lang} ({len(dictionaries[lang])} words)")
        except Exception as e:
            print(f"Error loading/migrating {filename}: {e}")
            dictionaries[lang] = {}
    else:
        print(f"Dictionary not found: {file_path}")
        dictionaries[lang] = {}

# Init databases
def init_json_files():
    if not os.path.exists(CONFIG_PATH):
        default_config = {
            "geminiApiKey": "",
            "openAiApiKey": "",
            "isAiEnabled": False,
            "aiPromptTemplate": "",
            "elevenLabsApiKey": "",
            "elevenLabsVoiceId": "21m00Tcm4TlvDq8ikWAM",
            "customDictionary": {},
            "rules": []
        }
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False)
            
    if not os.path.exists(CLIENT_KEYS_PATH):
        with open(CLIENT_KEYS_PATH, 'w', encoding='utf-8') as f:
            json.dump([], f, indent=2, ensure_ascii=False)

    if not os.path.exists(USERS_PATH):
        # Admin default user
        admin_pass_hash = hashlib.sha256("admin123".encode('utf-8')).hexdigest()
        default_users = [
            {
                "name": "Administrateur",
                "email": "admin@burkina.bf",
                "passwordHash": admin_pass_hash,
                "role": "admin"
            }
        ]
        with open(USERS_PATH, 'w', encoding='utf-8') as f:
            json.dump(default_users, f, indent=2, ensure_ascii=False)

init_json_files()

def load_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
            # Inject Env Overrides dynamically
            env_map = {
                'GEMINI_API_KEY': 'geminiApiKey',
                'OPENAI_API_KEY': 'openAiApiKey',
                'ELEVEN_LABS_API_KEY': 'elevenLabsApiKey',
                'ELEVEN_LABS_VOICE_ID': 'elevenLabsVoiceId'
            }
            config['envOverrides'] = {}
            for env_name, config_key in env_map.items():
                if os.environ.get(env_name):
                    config[config_key] = os.environ.get(env_name)
                    config['envOverrides'][config_key] = True
            return config
    except Exception:
        return {}

def save_config(config):
    # Strip env overrides before saving
    if 'envOverrides' in config:
        del config['envOverrides']
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def load_clients():
    try:
        with open(CLIENT_KEYS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def save_clients(clients):
    with open(CLIENT_KEYS_PATH, 'w', encoding='utf-8') as f:
        json.dump(clients, f, indent=2, ensure_ascii=False)

def load_users():
    try:
        with open(USERS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def save_users(users):
    with open(USERS_PATH, 'w', encoding='utf-8') as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

# AI API helpers
def call_gemini(text, target_lang_name, target_lang_key, api_key, dict_subset, rules_subset, custom_prompt=None):
    dict_str = json.dumps(dict_subset, ensure_ascii=False, indent=2)
    rules_str = "\n".join([f"- [Type: {r['type']}] Motif: \"{r['pattern']}\" -> Effet: \"{r['replacement']}\" ({r['description']})" for r in rules_subset])
    
    system_prompt = custom_prompt or f"""Vous êtes un linguiste expert et traducteur pour les langues du Burkina Faso.
Votre tâche consiste à traduire de manière réaliste, fluide et grammaticalement impeccable.

Détails de la traduction :
- Langue source : Français
- Langue cible : {target_lang_name} (Code: {target_lang_key})

Voici une sélection d'extraits du dictionnaire de l'application (Prioritaire) :
{dict_str}

Voici des règles grammaticales, orthographiques et phonétiques définies par les experts de l'application :
{rules_str}

NORMES DE TRANSCRIPTION ET D'ORTHOGRAPHE DU BURKINA FASO :
- Alphabet National : Respectez l'alphabet de base en vigueur (Commission Nationale des Langues) ; utilisez les caractères spécifiques comme 'ɛ' et 'ɔ' lorsque requis.
- Nasalisation : Notez-la en insérant la lettre 'n' immédiatement après la voyelle nasalisée (ex: voyelle + n).
- Longueur vocalique : Doublez la voyelle pour marquer une voyelle longue (ex: 'ee', 'oo') afin d'éviter toute confusion sémantique.
- Tons : Bien que non transcrits systématiquement dans l'écriture courante, respectez les intonations (tons haut, moyen et bas) pour la traduction, l'écriture phonétique et la prononciation.
- Emprunts : Pour les concepts modernes ou administratifs n'ayant pas de traduction traditionnelle directe, adaptez-les à la phonologie locale (ex: "mobili" pour véhicule en Dioula) plutôt que de faire un calque littéral ou d'employer le mot français brut.

CONSIGNES STRICTES :
1. CORRECTION DES FAUTES : Si le texte source en français contient des fautes d'orthographe ou de frappe, corrigez-les discrètement. Le champ "corrected_input" contiendra cette phrase corrigée.
2. SYNONYMES : Si un mot n'existe pas dans le dictionnaire local, cherchez un synonyme en Français présent dans le dictionnaire local.
3. TRADUCTION DE PHRASE : Ne faites pas du mot-à-mot.
4. GUIDE DE PRONONCIATION : Fournissez dans "phonetic" une transcription phonétique adaptée à la lecture française.

Format de réponse JSON strict obligatoire :
{{
  "corrected_input": "texte d'origine corrigé ou identique",
  "translation": "traduction de haute qualité dans la langue cible",
  "phonetic": "prononciation phonétique avec accents de tons ou conseils de lecture",
  "syllables": "Le découpage syllabique de la traduction séparé par des '/' (ex: 'Ne / y / yi / beo / go')",
  "vocal_writing": "L'écriture vocale sous forme de syllabes séparées par des tirets facilitant la prononciation correcte par une voix artificielle (ex: 'Nè-y-yi-bé-o-go' ou 'M-ma Ab-doul Ra-chid, A-li ya-gɛn-ga')",
  "rules_applied": ["règle 1 appliquée", "règle 2 appliquée"]
}}"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [
            {"parts": [{"text": f"Voici le texte à traduire : \"{text}\""}, {"text": system_prompt}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.15
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            resp_data = json.loads(response.read().decode('utf-8'))
            text_res = resp_data['candidates'][0]['content']['parts'][0]['text']
            return json.loads(text_res.strip())
    except Exception as e:
        print("Gemini API call failed:", e)
        return None

def call_openai(text, target_lang_name, target_lang_key, api_key, dict_subset, rules_subset, custom_prompt=None):
    dict_str = json.dumps(dict_subset, ensure_ascii=False, indent=2)
    rules_str = "\n".join([f"- [Type: {r['type']}] Motif: \"{r['pattern']}\" -> Effet: \"{r['replacement']}\" ({r['description']})" for r in rules_subset])
    
    system_prompt = custom_prompt or f"""Vous êtes un linguiste expert et traducteur pour les langues du Burkina Faso.
Détails de la traduction :
- Langue source : Français
- Langue cible : {target_lang_name} (Code: {target_lang_key})

Voici des extraits du dictionnaire :
{dict_str}

Règles :
{rules_str}

NORMES DE TRANSCRIPTION DU BURKINA FASO :
- Alphabet : Respectez l'alphabet national officiel (caractères comme 'ɛ' et 'ɔ' si nécessaire).
- Nasalisation : 'n' après la voyelle.
- Longueur vocalique : Redoublement de la voyelle (ex: 'ee', 'oo').
- Emprunts : Adaptation phonologique (ex: "mobili" en Dioula) des termes modernes/administratifs.

Format de réponse JSON strict :
{{
  "corrected_input": "texte d'origine corrigé ou identique",
  "translation": "traduction de haute qualité",
  "phonetic": "prononciation phonétique",
  "syllables": "découpage syllabique séparé par des '/'",
  "vocal_writing": "écriture vocale sous forme de syllabes séparées par des tirets (ex: 'Nè-y-yi-bé-o-go')",
  "rules_applied": []
}}"""

    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Voici le texte à traduire : \"{text}\""}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.15
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            resp_data = json.loads(response.read().decode('utf-8'))
            text_res = resp_data['choices'][0]['message']['content']
            return json.loads(text_res.strip())
    except Exception as e:
        print("OpenAI API call failed:", e)
        return None

def call_ai_rich_translation(text, target_lang, source_lang, target_lang_name, config):
    api_key = config.get('geminiApiKey')
    if not api_key:
        openai_key = config.get('openAiApiKey')
        if openai_key:
            return call_openai_rich_translation(text, target_lang, source_lang, target_lang_name, openai_key)
        return None
        
    system_prompt = f"""Vous êtes un linguiste expert en langues locales du Burkina Faso.
Traduisez le texte suivant :
- Texte source : "{text}" (Langue : {source_lang})
- Langue cible : {target_lang_name} (Code : {target_lang})

NORMES DE TRANSCRIPTION ET D'ORTHOGRAPHE DU BURKINA FASO :
- Alphabet National : Respectez l'alphabet de base en vigueur (Commission Nationale des Langues) ; utilisez les caractères spécifiques comme 'ɛ' et 'ɔ' lorsque requis.
- Nasalisation : Notez-la en insérant la lettre 'n' immédiatement après la voyelle nasalisée (ex: voyelle + n).
- Longueur vocalique : Doublez la voyelle pour marquer une voyelle longue (ex: 'ee', 'oo') afin d'éviter toute confusion sémantique.
- Tons : Bien que non transcrits systématiquement dans l'écriture courante, respectez les intonations (tons haut, moyen et bas) dans l'écriture phonétique et la prononciation vocale.
- Emprunts : Pour les concepts modernes ou administratifs n'ayant pas de traduction traditionnelle directe, adaptez-les à la phonologie locale (ex: "mobili" pour véhicule en Dioula) plutôt que de faire un calque littéral ou d'employer le mot français brut.

Vous devez absolument renvoyer une réponse au format JSON strict contenant les champs suivants :
- "translation" : La traduction exacte dans la langue locale (ex: "Ne y yibeoogo" pour bonjour).
- "syllables" : Le découpage syllabique séparé par des "/" (ex: "Ne / y / yi / beo / go").
- "vocal_writing" : L'écriture vocale sous forme de syllabes séparées par des tirets facilitant la prononciation correcte par une voix artificielle (ex: "Nè-y-yi-bé-o-go" pour bonjour, ou "M-ma Ab-doul Ra-chid, A-li ya-gɛn-ga" pour une phrase longue, séparez chaque bloc syllabique de chaque mot par des tirets).
- "category" : La catégorie grammaticale (ex: "Nom", "Verbe", "Interjection", "Phrase").
- "senses" : Le sens précis ou contexte d'utilisation.
- "example_fr" : Un court exemple d'utilisation en français.
- "example_local" : La traduction de cet exemple d'utilisation dans la langue locale cible.
- "confidence" : Un score décimal entre 0.0 et 1.0 indiquant votre niveau de certitude. Si vous n'êtes pas absolument sûr de la traduction locale exacte, ce score doit être inférieur à 0.8.

Consignes strictes :
1. Pas de mot-à-mot, respectez la grammaire et les expressions locales.
2. Si vous avez un doute ou n'êtes pas sûr, mettez un score de confidence inférieur à 0.8.
3. Renvoyez uniquement du JSON valide sans aucune explication extérieure.
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [
            {"parts": [{"text": system_prompt}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            resp_data = json.loads(response.read().decode('utf-8'))
            text_res = resp_data['candidates'][0]['content']['parts'][0]['text']
            return json.loads(text_res.strip())
    except Exception as e:
        print("Gemini Rich API call failed:", e)
        openai_key = config.get('openAiApiKey')
        if openai_key:
            return call_openai_rich_translation(text, target_lang, source_lang, target_lang_name, openai_key)
        return None

def call_openai_rich_translation(text, target_lang, source_lang, target_lang_name, api_key):
    system_prompt = f"""Vous êtes un linguiste expert en langues locales du Burkina Faso.
Traduisez le texte suivant :
- Texte source : "{text}" (Langue : {source_lang})
- Langue cible : {target_lang_name} (Code : {target_lang})

NORMES DE TRANSCRIPTION ET D'ORTHOGRAPHE DU BURKINA FASO :
- Alphabet National : Respectez l'alphabet de base en vigueur (Commission Nationale des Langues) ; utilisez les caractères spécifiques comme 'ɛ' et 'ɔ' lorsque requis.
- Nasalisation : Notez-la en insérant la lettre 'n' immédiatement après la voyelle nasalisée (ex: voyelle + n).
- Longueur vocalique : Doublez la voyelle pour marquer une voyelle longue (ex: 'ee', 'oo') afin d'éviter toute confusion sémantique.
- Tons : Bien que non transcrits systématiquement dans l'écriture courante, respectez les intonations (tons haut, moyen et bas).
- Emprunts : Pour les concepts modernes ou administratifs n'ayant pas de traduction traditionnelle directe, adaptez-les à la phonologie locale (ex: "mobili" pour véhicule en Dioula) plutôt que de faire un calque littéral ou d'employer le mot français brut.

Vous devez absolument renvoyer une réponse au format JSON strict contenant les champs suivants :
- "translation" : La traduction exacte dans la langue locale.
- "syllables" : Le découpage syllabique séparé par des "/".
- "vocal_writing" : L'écriture vocale sous forme de syllabes séparées par des tirets (ex: "Nè-y-yi-bé-o-go").
- "category" : La catégorie grammaticale.
- "senses" : Le sens ou contexte d'utilisation.
- "example_fr" : Exemple en français.
- "example_local" : Exemple en langue locale.
- "confidence" : Score décimal entre 0.0 et 1.0.

Consignes : Renvoyez uniquement du JSON valide."""

    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": system_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            resp_data = json.loads(response.read().decode('utf-8'))
            text_res = resp_data['choices'][0]['message']['content']
            return json.loads(text_res.strip())
    except Exception as e:
        print("OpenAI Rich API call failed:", e)
        return None

def call_ai_conversation(text, target_lang, target_lang_name, context, config):
    api_key = config.get('geminiApiKey')
    if not api_key:
        openai_key = config.get('openAiApiKey')
        if openai_key:
            return call_openai_conversation(text, target_lang, target_lang_name, context, openai_key)
        return None
        
    context_str = json.dumps(context, ensure_ascii=False)
    system_prompt = f"""Vous êtes un assistant conversationnel IA expert et fluide dans la langue locale du Burkina Faso : {target_lang_name} (code : {target_lang}).
Votre rôle est d'engager un dialogue constructif et d'être capable de discuter de manière fluide et naturelle dans la langue cible ({target_lang_name}) sur absolument tous les domaines (sciences, histoire, géographie, culture, technologie, vie quotidienne, etc.).

Consignes importantes pour la langue et les réponses :
1. FLUIDITÉ DE LA CONVERSATION EN LANGUE LOCALE : Discutez de manière naturelle et fluide en {target_lang_name}. Si l'utilisateur s'adresse à vous ou attend une réponse en {target_lang_name}, répondez entièrement et de façon détaillée dans cette langue locale.
2. UTILISATION DE VOTRE PROPRE BASE DE CONNAISSANCES : N'hésitez pas à puiser dans votre propre base de connaissances étendue pour expliquer des concepts complexes ou répondre à des questions scientifiques, historiques ou générales, le tout formulé en {target_lang_name}.
3. MOTS OU CONCEPTS INEXISTANTS : Si un concept moderne, scientifique ou technique n'existe pas directement dans le vocabulaire traditionnel de la langue cible ({target_lang_name}), ne restez pas bloqué. Utilisez intelligemment des synonymes proches, des périphrases explicatives, ou des descriptions imagées dans la langue locale pour l'exprimer au mieux.
4. Richesse culturelle : Saluez chaleureusement et respectez les codes de politesse burkinabè.

NORMES DE TRANSCRIPTION ET D'ORTHOGRAPHE DU BURKINA FASO :
- Alphabet National : Respectez l'alphabet de base en vigueur (Commission Nationale des Langues) ; utilisez les caractères spécifiques comme 'ɛ' et 'ɔ' lorsque requis.
- Nasalisation : Notez-la en insérant la lettre 'n' immédiatement après la voyelle nasalisée (ex: voyelle + n).
- Longueur vocalique : Doublez la voyelle pour marquer une voyelle longue (ex: 'ee', 'oo') afin d'éviter toute confusion sémantique.
- Tons : Bien que non transcrits systématiquement dans l'écriture courante, respectez les intonations (tons haut, moyen et bas) dans l'écriture phonétique et la prononciation vocale.
- Emprunts : Pour les concepts modernes ou administratifs n'ayant pas de traduction traditionnelle directe, adaptez-les à la phonologie locale (ex: "mobili" pour véhicule en Dioula) plutôt que de faire un calque littéral ou d'employer le mot français brut.

Historique des échanges :
{context_str}

L'utilisateur dit : "{text}"

Vous devez obligatoirement répondre sous la forme d'un objet JSON strict contenant exactement les champs suivants :
- "response_text" : Votre message ou réponse de conversation (rédigé principalement en langue cible {target_lang_name} de manière fluide et naturelle, ou en Français si l'échange le justifie, mais priorisez une discussion fluide en {target_lang_name} pour répondre aux questions formulées dans cette langue).
- "translation" : Si l'utilisateur a demandé comment traduire ou dire un mot/phrase spécifique, donnez la traduction exacte de l'expression demandée. Sinon, laissez vide ou reprenez le terme clé concerné.
- "syllables" : Le découpage syllabique de la traduction ou de l'expression clé en langue cible, séparé par des "/" (ex: "Ne / y / yi / beo / go").
- "vocal_writing" : L'écriture vocale sous forme de syllabes séparées par des tirets (ex: "Nè-y-yi-bé-o-go" ou "M-ma Ab-doul Ra-chid, A-li ya-gɛn-ga").
- "explanation" : Les explications culturelles, linguistiques ou conseils de prononciation (rédigés en Français pour aider l'utilisateur à comprendre).
- "example" : Un exemple d'usage conversationnel (ex: "Utilisateur: [phrase] -> Assistant: [réponse]").
- "confidence" : Niveau de confiance entre 0.0 et 1.0.

Soignez la politesse et la convivialité locales."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [
            {"parts": [{"text": system_prompt}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.3
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            resp_data = json.loads(response.read().decode('utf-8'))
            text_res = resp_data['candidates'][0]['content']['parts'][0]['text']
            return json.loads(text_res.strip())
    except Exception as e:
        print("Gemini Conversation call failed:", e)
        openai_key = config.get('openAiApiKey')
        if openai_key:
            return call_openai_conversation(text, target_lang, target_lang_name, context, openai_key)
        return None

def call_openai_conversation(text, target_lang, target_lang_name, context, api_key):
    context_str = json.dumps(context, ensure_ascii=False)
    system_prompt = f"""Vous êtes un assistant conversationnel IA expert et fluide dans la langue locale du Burkina Faso : {target_lang_name} (code : {target_lang}).
Votre rôle est d'engager un dialogue constructif et d'être capable de discuter fluidement sur absolument tous les domaines (sciences, histoire, géographie, culture, technologie, vie quotidienne, etc.) directement en {target_lang_name}.

Consignes :
1. FLUIDITÉ DE LA CONVERSATION : Discutez de manière naturelle et fluide en {target_lang_name}. Si l'utilisateur attend ou formule une question en {target_lang_name}, répondez-lui entièrement et de façon détaillée dans cette langue locale.
2. BASE DE CONNAISSANCES : Utilisez votre propre base de connaissances étendue pour répondre de manière approfondie et précise.
3. CONCEPTS INEXISTANTS : Utilisez des synonymes ou des périphrases explicatives pour exprimer les termes modernes ou inexistants en {target_lang_name}.

NORMES DE TRANSCRIPTION ET D'ORTHOGRAPHE DU BURKINA FASO :
- Alphabet : Respectez l'alphabet national officiel (ɛ, ɔ).
- Nasalisation : 'n' après la voyelle.
- Longueur vocalique : Redoublement de la voyelle (ee, oo).
- Emprunts : Adaptation phonologique (ex: "mobili" en Dioula) des termes modernes/administratifs.

Historique : {context_str}
Demande de l'utilisateur : "{text}"

Format JSON strict attendu :
{{
  "response_text": "Votre réponse conversationnelle principale (en {target_lang_name} ou français, prioritairement en {target_lang_name} si l'utilisateur s'exprime dans cette langue)",
  "translation": "La traduction demandée ou expression clé concernée",
  "syllables": "Découpage syllabique",
  "vocal_writing": "Écriture phonétique sous forme de syllabes séparées par des tirets (ex: 'Nè-y-yi-bé-o-go')",
  "explanation": "Explications linguistiques ou culturelles (en français)",
  "example": "Exemple d'usage",
  "confidence": 0.9
}}
Consignes : Renvoyez uniquement du JSON valide."""

    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": system_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            resp_data = json.loads(response.read().decode('utf-8'))
            text_res = resp_data['choices'][0]['message']['content']
            return json.loads(text_res.strip())
    except Exception as e:
        print("OpenAI Conversation call failed:", e)
        return None

# Local rules-based engine fallback
def local_translate(text, lang_key, rules, custom_dict):
    corrected = text
    spelling_applied = []
    active_spelling = [r for r in rules if r.get('isActive', True) and r.get('language') == lang_key and r.get('type') == 'spelling']
    
    for r in active_spelling:
        pattern = r.get('pattern', '')
        replacement = r.get('replacement', '')
        if pattern:
            try:
                reg = re.compile(r'\b' + re.escape(pattern) + r'\b', re.IGNORECASE)
                if reg.search(corrected):
                    corrected = reg.sub(replacement, corrected)
                    spelling_applied.append(f"Correction orthographe: {pattern} -> {replacement}")
            except Exception:
                pass

    merged_dict = {}
    if lang_key in dictionaries:
        merged_dict.update(dictionaries[lang_key])
    merged_dict.update(custom_dict.get(lang_key, {}))
    
    words = corrected.split()
    translated_words = []
    
    for w in words:
        clean_word = re.sub(r'[.,!?;:()\'"\\/]', '', w).lower().strip()
        if clean_word in merged_dict:
            entry = merged_dict[clean_word]
            if isinstance(entry, dict):
                translated_words.append(entry.get("translation", ""))
            else:
                translated_words.append(entry)
        else:
            translated_words.append(f"[{w}]")
            
    translated = " ".join(translated_words)

    grammar_applied = []
    active_grammar = [r for r in rules if r.get('isActive', True) and r.get('language') == lang_key and r.get('type') == 'grammar']
    for r in active_grammar:
        pattern = r.get('pattern', '')
        replacement = r.get('replacement', '')
        if pattern:
            try:
                reg = re.compile(pattern, re.IGNORECASE)
                if reg.search(translated):
                    translated = reg.sub(replacement, translated)
                    grammar_applied.append(f"Règle de grammaire: {pattern} -> {replacement}")
            except Exception:
                pass

    return {
        "corrected_input": corrected,
        "translation": translated,
        "phonetic": "",
        "rules_applied": spelling_applied + grammar_applied
    }

def simulate_conversation_fallback(text, target_lang, target_lang_name, config):
    # Try to clean the text
    clean_text = re.sub(r'[.,!?;:]', '', text).lower().strip()
    
    # Check if we have common phrases
    simulated_responses = {
        "bonjour": {
            "moore": {
                "response_text": "Ne y yibeoogo ! (Simulation IA) Comment allez-vous ce matin ?",
                "translation": "Ne y yibeoogo",
                "syllables": "Ne / y / yi / beo / go",
                "vocal_writing": "Nè y yi-bé-o-go",
                "explanation": "Salutation du matin en Mooré.",
                "example": "Utilisateur: Bonjour -> IA: Ne y yibeoogo, laafi beeme ?"
            },
            "dioula": {
                "response_text": "I ni sɔgɔma ! (Simulation IA) Comment se passe votre journée ?",
                "translation": "I ni sɔgɔma",
                "syllables": "I / ni / sɔ / gɔ / ma",
                "vocal_writing": "I ni sɔgɔma",
                "explanation": "Salutation standard du matin en Dioula.",
                "example": "Utilisateur: Bonjour -> IA: I ni sɔgɔma, i kènè wa?"
            },
            "fulfulde": {
                "response_text": "Jam waali ! (Simulation IA) Comment allez-vous ?",
                "translation": "Jam waali",
                "syllables": "Jam / waa / li",
                "vocal_writing": "Jam waali",
                "explanation": "Salutation du matin en Fulfuldé.",
                "example": "Utilisateur: Bonjour -> IA: Jam waali, mbandu jam ?"
            },
            "gourounsi": {
                "response_text": "A ni yassoro ! (Simulation IA)",
                "translation": "A ni yassoro",
                "syllables": "A / ni / yas / so / ro",
                "vocal_writing": "A ni yassoro",
                "explanation": "Salutation standard en Gourounsi.",
                "example": "Utilisateur: Bonjour -> IA: A ni yassoro"
            },
            "bissa": {
                "response_text": "A ni kiirou ! (Simulation IA)",
                "translation": "A ni kiirou",
                "syllables": "A / ni / kii / rou",
                "vocal_writing": "A ni kiirou",
                "explanation": "Salutation standard en Bissa.",
                "example": "Utilisateur: Bonjour -> IA: A ni kiirou"
            }
        },
        "comment ca va": {
            "moore": {
                "response_text": "Laafi beeme ! Tout va bien ici, merci. Et vous ?",
                "translation": "laafi beeme ?",
                "syllables": "laa / fi / bee / me",
                "vocal_writing": "laafi beeme",
                "explanation": "Expression classique pour demander comment ça va en Mooré.",
                "example": "Utilisateur: Comment ça va ? -> IA: Laafi beeme !"
            },
            "dioula": {
                "response_text": "I kènè wa ! Tout va bien en Dioula. Et chez vous ?",
                "translation": "i kènè wa ?",
                "syllables": "i / kè / nè / wa",
                "vocal_writing": "i kènè wa",
                "explanation": "Formule pour s'enquérir de la santé en Dioula.",
                "example": "Utilisateur: Comment ça va ? -> IA: I kènè wa !"
            }
        },
        "ca va": {
            "moore": {
                "response_text": "Laafi beeme ! Tout va bien ici, merci. Et vous ?",
                "translation": "laafi beeme ?",
                "syllables": "laa / fi / bee / me",
                "vocal_writing": "laafi beeme",
                "explanation": "Expression classique pour demander comment ça va en Mooré.",
                "example": "Utilisateur: Comment ça va ? -> IA: Laafi beeme !"
            },
            "dioula": {
                "response_text": "I kènè wa ! Tout va bien en Dioula. Et chez vous ?",
                "translation": "i kènè wa ?",
                "syllables": "i / kè / nè / wa",
                "vocal_writing": "i kènè wa",
                "explanation": "Formule pour s'enquérir de la santé en Dioula.",
                "example": "Utilisateur: Comment ça va ? -> IA: I kènè wa !"
            }
        },
        "merci": {
            "moore": {
                "response_text": "Barka ! (Simulation IA) Tout le plaisir est pour moi.",
                "translation": "barka",
                "syllables": "bar / ka",
                "vocal_writing": "barka",
                "explanation": "Remerciement standard.",
                "example": "Utilisateur: Merci -> IA: Barka !"
            },
            "dioula": {
                "response_text": "A ni kè ! (Simulation IA) C'est un plaisir de vous aider.",
                "translation": "a ni kè",
                "syllables": "a / ni / kè",
                "vocal_writing": "a ni kè",
                "explanation": "Remerciement ou salutation du travail en Dioula.",
                "example": "Utilisateur: Merci -> IA: A ni kè !"
            }
        }
    }
    
    # Check for direct or partial match
    matched_entry = None
    for key, langs in simulated_responses.items():
        if key in clean_text:
            matched_entry = langs.get(target_lang)
            if matched_entry:
                break
                
    if matched_entry:
        return {
            "response_text": matched_entry["response_text"],
            "translation": matched_entry["translation"],
            "syllables": matched_entry["syllables"],
            "vocal_writing": matched_entry["vocal_writing"],
            "explanation": matched_entry["explanation"],
            "example": matched_entry["example"],
            "confidence": 0.9
        }
        
    # Default word translation simulation
    local_res = local_translate(text, target_lang, config.get("rules", []), config.get("customDictionary", {}))
    translation = local_res["translation"]
    
    # Look up phonetic and syllables for the first matching word if possible
    syllables = ""
    vocal_writing = translation
    explanation = f"Traduction simulée mot-à-mot (Langue: {target_lang_name})."
    
    words = text.split()
    for w in words:
        clean_w = re.sub(r'[.,!?;:]', '', w).lower().strip()
        entry = dictionaries.get(target_lang, {}).get(clean_w)
        if entry and isinstance(entry, dict):
            if entry.get("syllables"):
                syllables = entry.get("syllables")
            if entry.get("vocal_writing"):
                vocal_writing = entry.get("vocal_writing")
            if entry.get("senses"):
                explanation = f"Explication de '{clean_w}' : {entry.get('senses')}. " + explanation
            break

    return {
        "response_text": f"Message reçu : '{text}'. (Simulateur IA) En {target_lang_name}, on traduit généralement par : {translation}",
        "translation": translation,
        "syllables": syllables,
        "vocal_writing": vocal_writing,
        "explanation": explanation,
        "example": f"Exemple : '{text}' se dit '{translation}'",
        "confidence": 0.6
    }

class UnifiedHandler(http.server.BaseHTTPRequestHandler):
    def authenticate_client(self, required_lang=None):
        api_key = self.headers.get('X-API-Key')
        if not api_key:
            auth_header = self.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                api_key = auth_header[7:]

        is_local_client_fallback = False
        if not api_key:
            is_local_client_fallback = True
            # Fallback to local active developer key if none is provided
            clients = load_clients()
            active_clients = [c for c in clients if c.get('isActive', True) and c.get('status') == 'active']
            if active_clients:
                # Prioritize a client that has permission for the required language
                matched = next((c for c in active_clients if not required_lang or required_lang in c.get('languages', [])), None)
                if matched:
                    api_key = matched.get('apiKey')
                else:
                    api_key = active_clients[0].get('apiKey')

        if not api_key:
            return None, (401, "Authentification requise. Header 'X-API-Key' ou Authorization Bearer manquant.")

        clients = load_clients()
        client = next((c for c in clients if c.get('apiKey') == api_key), None)

        if not client:
            return None, (401, "Clé API invalide.")

        if not client.get('isActive', True) or client.get('status') != 'active':
            return None, (403, "Cette clé API est désactivée ou suspendue.")

        if required_lang and not is_local_client_fallback:
            allowed_langs = client.get('languages', [])
            if allowed_langs and required_lang not in allowed_langs:
                return None, (403, f"Langue '{required_lang}' non autorisée pour cette clé API.")

        max_quota = int(client.get('quota', 1000))
        usage_count = int(client.get('usage', 0))
        if usage_count >= max_quota:
            return None, (429, "Quota d'appels API dépassé pour cette clé.")

        # Update stats
        client['usage'] = usage_count + 1
        client['lastUsed'] = datetime.utcnow().isoformat() + 'Z'
        save_clients(clients)

        return client, None

    def handle_extended_api(self, path, post_data):
        try:
            body = json.loads(post_data.decode('utf-8')) if post_data else {}
        except Exception:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": "Format JSON invalide."}, ensure_ascii=False).encode('utf-8'))
            return

        text = body.get('text', '').strip()
        target_lang = body.get('target_lang', '').strip().lower()
        source_lang = body.get('source_lang', 'fr').strip().lower()
        
        norm_path = path.replace('/api/v1', '')
        
        client, err = self.authenticate_client(target_lang if target_lang else None)
        if err:
            status_code, err_msg = err
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": err_msg}, ensure_ascii=False).encode('utf-8'))
            return

        lang_names = {
            'moore': 'Mooré',
            'dioula': 'Dioula',
            'fulfulde': 'Fulfuldé',
            'gourounsi': 'Gourounsi',
            'bissa': 'Bissa'
        }
        target_lang_name = lang_names.get(target_lang, target_lang.capitalize())
        config = load_config()

        response_data = {
            "success": True,
            "input": text,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "translation": "",
            "syllables": "",
            "vocal_reading": "",
            "example": "",
            "confidence": 1.0,
            "validation_status": "pending_human_validation"
        }

        if norm_path == '/translate-word':
            if not text or not target_lang:
                self.send_error_json(400, "Champs 'text' et 'target_lang' requis.")
                return
            
            ai_res = None
            if config.get("isAiEnabled"):
                ai_res = call_ai_rich_translation(text, target_lang, source_lang, target_lang_name, config)
            
            if ai_res:
                response_data["translation"] = ai_res.get("translation", "")
                response_data["syllables"] = ai_res.get("syllables", "")
                response_data["vocal_reading"] = ai_res.get("vocal_writing", "")
                response_data["example"] = f"{ai_res.get('example_fr', '')} → {ai_res.get('example_local', '')}"
                response_data["confidence"] = ai_res.get("confidence", 0.7)
                response_data["category"] = ai_res.get("category", "")
                response_data["senses"] = ai_res.get("senses", "")
                if response_data["confidence"] < 0.8:
                    response_data["translation"] = response_data["translation"] + " (Je ne suis pas certain de cette traduction. Une validation humaine est recommandée.)"
            else:
                dict_entry = dictionaries.get(target_lang, {}).get(text.lower())
                if dict_entry:
                    response_data["translation"] = dict_entry.get("translation", "")
                    response_data["syllables"] = dict_entry.get("syllables", "")
                    response_data["vocal_reading"] = dict_entry.get("vocal_writing", "")
                    response_data["example"] = f"{dict_entry.get('example_fr', '')} → {dict_entry.get('example_local', '')}"
                    response_data["confidence"] = dict_entry.get("confidence", 1.0)
                    response_data["validation_status"] = "validated" if dict_entry.get("validated", False) else "pending_human_validation"
                    response_data["category"] = dict_entry.get("category", "Inconnu")
                    response_data["senses"] = dict_entry.get("senses", "")
                    response_data["dialect"] = dict_entry.get("dialect", "Standard")
                    response_data["audio_remark"] = dict_entry.get("audio_remark", "")
                    response_data["reading_rhythm"] = dict_entry.get("reading_rhythm", "normal")
                    response_data["tone_accent"] = dict_entry.get("tone_accent", "")
                else:
                    self.fill_local_fallback(response_data, text, target_lang, config)

        elif norm_path == '/translate-sentence':
            if not text or not target_lang:
                self.send_error_json(400, "Champs 'text' et 'target_lang' requis.")
                return
            
            if config.get("isAiEnabled"):
                ai_res = call_ai_rich_translation(text, target_lang, source_lang, target_lang_name, config)
                if ai_res:
                    response_data["translation"] = ai_res.get("translation", "")
                    response_data["syllables"] = ai_res.get("syllables", "")
                    response_data["vocal_reading"] = ai_res.get("vocal_writing", "")
                    response_data["example"] = f"{ai_res.get('example_fr', '')} → {ai_res.get('example_local', '')}"
                    response_data["confidence"] = ai_res.get("confidence", 0.7)
                    response_data["category"] = ai_res.get("category", "")
                    response_data["senses"] = ai_res.get("senses", "")
                    if response_data["confidence"] < 0.8:
                        response_data["translation"] = response_data["translation"] + " (Je ne suis pas certain de cette traduction. Une validation humaine est recommandée.)"
                else:
                    self.fill_local_fallback(response_data, text, target_lang, config)
            else:
                self.fill_local_fallback(response_data, text, target_lang, config)

        elif norm_path == '/conversation':
            context = body.get('context', [])
            if not text or not target_lang:
                self.send_error_json(400, "Champs 'text' et 'target_lang' requis.")
                return
            
            ai_res = None
            if config.get("isAiEnabled"):
                ai_res = call_ai_conversation(text, target_lang, target_lang_name, context, config)
            
            if ai_res:
                response_data["translation"] = ai_res.get("translation", "")
                response_data["syllables"] = ai_res.get("syllables", "")
                response_data["vocal_reading"] = ai_res.get("vocal_writing", "")
                response_data["example"] = ai_res.get("example", "")
                response_data["confidence"] = ai_res.get("confidence", 0.8)
                response_data["response_text"] = ai_res.get("response_text", "")
                response_data["explanation"] = ai_res.get("explanation", "")
            else:
                sim_res = simulate_conversation_fallback(text, target_lang, target_lang_name, config)
                response_data["translation"] = sim_res.get("translation", "")
                response_data["syllables"] = sim_res.get("syllables", "")
                response_data["vocal_reading"] = sim_res.get("vocal_writing", "")
                response_data["example"] = sim_res.get("example", "")
                response_data["confidence"] = sim_res.get("confidence", 0.6)
                response_data["response_text"] = sim_res.get("response_text", "")
                response_data["explanation"] = sim_res.get("explanation", "")

        elif norm_path == '/pronunciation':
            if not text:
                self.send_error_json(400, "Champ 'text' requis.")
                return
            response_data["vocal_reading"] = self.compute_vocal_writing(text, target_lang, config)
            response_data["translation"] = text

        elif norm_path == '/syllables':
            if not text:
                self.send_error_json(400, "Champ 'text' requis.")
                return
            response_data["syllables"] = self.compute_syllables(text, target_lang, config)
            response_data["translation"] = text

        elif norm_path == '/voice-ready-text':
            if not text:
                self.send_error_json(400, "Champ 'text' requis.")
                return
            response_data["vocal_reading"] = self.compute_vocal_writing(text, target_lang, config)
            response_data["translation"] = text

        elif norm_path == '/language-detect':
            if not text:
                self.send_error_json(400, "Champ 'text' requis.")
                return
            detected = self.detect_language(text, config)
            response_data["source_lang"] = detected
            response_data["translation"] = text
            response_data["confidence"] = 0.9

        elif norm_path == '/dictionary-search':
            query = body.get('query', '').strip().lower()
            if not query or not target_lang:
                self.send_error_json(400, "Champs 'query' et 'target_lang' requis.")
                return
            
            results = []
            dict_to_search = dictionaries.get(target_lang, {})
            for k, entry in dict_to_search.items():
                if query in k or query in entry.get("translation", "").lower():
                    results.append({
                        "french": k,
                        "details": entry
                    })
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "results": results}, ensure_ascii=False).encode('utf-8'))
            return

        elif norm_path == '/validate-translation':
            french = body.get('french', '').strip().lower()
            validated = body.get('validated', True)
            if not french or not target_lang:
                self.send_error_json(400, "Champs 'french' et 'target_lang' requis.")
                return
            
            if target_lang in dictionaries and french in dictionaries[target_lang]:
                dictionaries[target_lang][french]["validated"] = validated
                dictionaries[target_lang][french]["confidence"] = 1.0 if validated else 0.8
                
                filename = DICTIONARY_FILES.get(target_lang)
                if filename:
                    file_path = os.path.join(ROOT_DIR, filename)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        json.dump(dictionaries[target_lang], f, indent=2, ensure_ascii=False)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "message": "Statut de validation mis à jour."}, ensure_ascii=False).encode('utf-8'))
                return
            else:
                self.send_error_json(404, "Mot non trouvé dans le dictionnaire.")
                return

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response_data, ensure_ascii=False).encode('utf-8'))

    def fill_local_fallback(self, response_data, text, target_lang, config):
        fallback_res = local_translate(text, target_lang, config.get("rules", []), config.get("customDictionary", {}))
        response_data["translation"] = fallback_res.get("translation", "")
        response_data["confidence"] = 0.5
        response_data["vocal_reading"] = self.compute_vocal_writing(fallback_res.get("translation", ""), target_lang, config)
        response_data["syllables"] = self.compute_syllables(fallback_res.get("translation", ""), target_lang, config)
        response_data["category"] = "Phrase" if len(text.split()) > 1 else "Mot"
        response_data["senses"] = f"Traduction standard de {text}"
        response_data["dialect"] = "Standard"

    def send_error_json(self, code, message):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"success": False, "error": message}, ensure_ascii=False).encode('utf-8'))

    def compute_vocal_writing(self, text, lang, config):
        words = text.split()
        res = []
        dict_lang = dictionaries.get(lang, {})
        for w in words:
            clean_word = re.sub(r'[.,!?;:()\'"\\/]', '', w).lower().strip()
            punc = re.search(r'[.,!?;:]+$', w)
            punc_str = punc.group(0) if punc else ""
            
            vocal = None
            found = False
            for fr_w, entry in dict_lang.items():
                if isinstance(entry, dict) and entry.get("translation", "").lower() == clean_word:
                    vocal = entry.get("vocal_writing") or entry.get("syllables") or entry.get("translation")
                    found = True
                    break
            if not found:
                if clean_word in dict_lang:
                    entry = dict_lang[clean_word]
                    vocal = entry.get("vocal_writing") if (isinstance(entry, dict) and entry.get("vocal_writing")) else (entry.get("syllables") if isinstance(entry, dict) else entry)
                else:
                    vocal = w
            
            if vocal:
                # Clean up duplicate spaces/slashes/hyphens into single hyphens for correct syllable reading rhythm
                vocal_clean = re.sub(r'\s*[\/\-\s]\s*', '-', vocal)
                vocal_clean = re.sub(r'\-+', '-', vocal_clean).strip('-')
                res.append(vocal_clean + punc_str)
            else:
                res.append(w)
        return " ".join(res)

    def compute_syllables(self, text, lang, config):
        words = text.split()
        res = []
        dict_lang = dictionaries.get(lang, {})
        for w in words:
            clean_word = re.sub(r'[.,!?;:()\'"\\/]', '', w).lower().strip()
            found = False
            for fr_w, entry in dict_lang.items():
                if isinstance(entry, dict) and entry.get("translation", "").lower() == clean_word:
                    res.append(entry.get("syllables") or entry.get("translation"))
                    found = True
                    break
            if not found:
                if clean_word in dict_lang:
                    entry = dict_lang[clean_word]
                    res.append(entry.get("syllables") if isinstance(entry, dict) else entry)
                else:
                    res.append(w)
        return " / ".join(res)

    def detect_language(self, text, config):
        lower = text.lower()
        if any(w in lower for w in ["ne y", "yibeogo", "laafi", "wẽnnaam"]):
            return "moore"
        elif any(w in lower for w in ["ani", "sogoma", "herra", "mobili", "ala"]):
            return "dioula"
        elif any(w in lower for w in ["jam", "selli", "mbandu"]):
            return "fulfulde"
        else:
            return "fr"

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, DELETE, PUT')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-API-Key, Authorization')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        # Serve frontend pages
        if self.path == '/':
            file_path = os.path.join(ROOT_DIR, 'frontend', 'client', 'dictionnaire_complet.html')
            self.serve_file(file_path, 'text/html')
            return
        elif self.path == '/admin' or self.path == '/admin/':
            file_path = os.path.join(ROOT_DIR, 'frontend', 'admin', 'espace_professeur.html')
            self.serve_file(file_path, 'text/html')
            return

        # API: Admin get configs
        elif self.path == '/admin/api/state':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            config = load_config()
            self.wfile.write(json.dumps(config, ensure_ascii=False).encode('utf-8'))
            return
            
        elif self.path == '/admin/api/clients':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            clients = load_clients()
            self.wfile.write(json.dumps(clients, ensure_ascii=False).encode('utf-8'))
            return

        # API: Client view own keys
        elif self.path.startswith('/api/client/my-keys'):
            # Parse email from query parameter
            email = ""
            query_match = re.search(r'email=([^&]+)', self.path)
            if query_match:
                import urllib.parse
                email = urllib.parse.unquote(query_match.group(1))
            
            clients = load_clients()
            user_keys = [c for c in clients if c.get('userEmail') == email]
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(user_keys, ensure_ascii=False).encode('utf-8'))
        elif self.path == '/api/v1/dictionaries':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(dictionaries, ensure_ascii=False).encode('utf-8'))
            return

        # Fallback server static assets
        else:
            clean_path = self.path.split('?')[0].lstrip('/')
            file_path = os.path.join(ROOT_DIR, clean_path)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                content_type = 'text/plain'
                if file_path.endswith('.html'): content_type = 'text/html'
                elif file_path.endswith('.css'): content_type = 'text/css'
                elif file_path.endswith('.js'): content_type = 'application/javascript'
                self.serve_file(file_path, content_type)
                return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"404 - Non trouve")

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)

        # ─── EXTENDED API ───
        extended_endpoints = [
            '/api/v1/translate-word', '/translate-word',
            '/api/v1/translate-sentence', '/translate-sentence',
            '/api/v1/conversation', '/conversation',
            '/api/v1/pronunciation', '/pronunciation',
            '/api/v1/syllables', '/syllables',
            '/api/v1/voice-ready-text', '/voice-ready-text',
            '/api/v1/language-detect', '/language-detect',
            '/api/v1/dictionary-search', '/dictionary-search',
            '/api/v1/validate-translation', '/validate-translation'
        ]
        if self.path in extended_endpoints:
            self.handle_extended_api(self.path, post_data)
            return

        # ─── ADMIN API ───
        if self.path == '/admin/api/state':
            try:
                payload = json.loads(post_data.decode('utf-8'))
                save_config(payload)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            except Exception as e:
                self.send_error(400, f"JSON invalide: {e}")
            return

        elif self.path == '/admin/api/clients':
            try:
                payload = json.loads(post_data.decode('utf-8'))
                save_clients(payload)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            except Exception as e:
                self.send_error(400, f"JSON invalide: {e}")
            return

        elif self.path == '/admin/api/approve-key':
            try:
                payload = json.loads(post_data.decode('utf-8'))
                req_id = payload.get('id')
                quota = int(payload.get('quota', 1000))
                languages = payload.get('languages', [])
                
                clients = load_clients()
                client = next((c for c in clients if c.get('id') == req_id), None)
                if client:
                    rand_bytes = hashlib.sha256(os.urandom(16)).hexdigest()[:32]
                    client['apiKey'] = 'bk_live_' + rand_bytes
                    client['quota'] = quota
                    client['languages'] = languages
                    client['isActive'] = True
                    client['status'] = 'active'
                    client['approvedAt'] = datetime.utcnow().isoformat() + 'Z'
                    save_clients(clients)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
                else:
                    self.send_response(404)
                    self.end_headers()
            except Exception as e:
                self.send_error(400, str(e))
            return

        elif self.path == '/admin/api/reject-key':
            try:
                payload = json.loads(post_data.decode('utf-8'))
                req_id = payload.get('id')
                
                clients = load_clients()
                client = next((c for c in clients if c.get('id') == req_id), None)
                if client:
                    client['isActive'] = False
                    client['status'] = 'rejected'
                    save_clients(clients)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
                else:
                    self.send_response(404)
                    self.end_headers()
            except Exception as e:
                self.send_error(400, str(e))
            return

        elif self.path == '/admin/api/dictionaries/update':
            try:
                payload = json.loads(post_data.decode('utf-8'))
                lang = payload.get('lang')
                french = payload.get('french', '').strip().lower()
                translation = payload.get('translation', '').strip()
                
                if lang in dictionaries and french and translation:
                    existing = dictionaries[lang].get(french, {})
                    if not isinstance(existing, dict):
                        existing = {"translation": existing}
                        
                    entry = {
                        "translation": translation,
                        "category": payload.get('category', existing.get('category', 'Inconnu')),
                        "senses": payload.get('senses', existing.get('senses', f"Traduction de {french}")),
                        "example_fr": payload.get('example_fr', existing.get('example_fr', '')),
                        "example_local": payload.get('example_local', existing.get('example_local', '')),
                        "dialect": payload.get('dialect', existing.get('dialect', 'Standard')),
                        "confidence": float(payload.get('confidence', existing.get('confidence', 1.0 if payload.get('validated') else 0.8))),
                        "validated": bool(payload.get('validated', existing.get('validated', False))),
                        "syllables": payload.get('syllables', existing.get('syllables', '')),
                        "phonetic": payload.get('phonetic', existing.get('phonetic', '')),
                        "vocal_writing": payload.get('vocal_writing', existing.get('vocal_writing', translation)),
                        "reading_rhythm": payload.get('reading_rhythm', existing.get('reading_rhythm', 'normal')),
                        "tone_accent": payload.get('tone_accent', existing.get('tone_accent', '')),
                        "audio_remark": payload.get('audio_remark', existing.get('audio_remark', ''))
                    }
                    dictionaries[lang][french] = entry
                    filename = DICTIONARY_FILES.get(lang)
                    if filename:
                        file_path = os.path.join(ROOT_DIR, filename)
                        with open(file_path, 'w', encoding='utf-8') as f:
                            json.dump(dictionaries[lang], f, indent=2, ensure_ascii=False)
                        
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
                        return
                self.send_response(400)
                self.end_headers()
            except Exception as e:
                self.send_error(500, str(e))
            return

        elif self.path == '/admin/api/dictionaries/delete':
            try:
                payload = json.loads(post_data.decode('utf-8'))
                lang = payload.get('lang')
                french = payload.get('french', '').strip().lower()
                
                if lang in dictionaries and french:
                    if french in dictionaries[lang]:
                        del dictionaries[lang][french]
                        filename = DICTIONARY_FILES.get(lang)
                        if filename:
                            file_path = os.path.join(ROOT_DIR, filename)
                            with open(file_path, 'w', encoding='utf-8') as f:
                                json.dump(dictionaries[lang], f, indent=2, ensure_ascii=False)
                            
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/json')
                            self.end_headers()
                            self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
                            return
                self.send_response(400)
                self.end_headers()
            except Exception as e:
                self.send_error(500, str(e))
            return

        # ─── AUTHENTICATION API ───
        elif self.path == '/api/auth/register':
            try:
                payload = json.loads(post_data.decode('utf-8'))
                name = payload.get('name', '').strip()
                email = payload.get('email', '').strip().lower()
                password = payload.get('password', '')

                if not name or not email or not password:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "error": "Tous les champs sont obligatoires."}, ensure_ascii=False).encode('utf-8'))
                    return

                users = load_users()
                if any(u.get('email') == email for u in users):
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "error": "Cet e-mail est déjà enregistré."}, ensure_ascii=False).encode('utf-8'))
                    return

                new_user = {
                    "name": name,
                    "email": email,
                    "passwordHash": hashlib.sha256(password.encode('utf-8')).hexdigest(),
                    "role": "client"
                }
                users.append(new_user)
                save_users(users)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            except Exception as e:
                self.send_error(500, str(e))
            return

        elif self.path == '/api/auth/login':
            try:
                payload = json.loads(post_data.decode('utf-8'))
                email = payload.get('email', '').strip().lower()
                password = payload.get('password', '')

                users = load_users()
                pass_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
                user = next((u for u in users if u.get('email') == email and u.get('passwordHash') == pass_hash), None)

                if user:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "success": True,
                        "user": {
                            "name": user.get('name'),
                            "email": user.get('email'),
                            "role": user.get('role')
                        }
                    }).encode('utf-8'))
                else:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "error": "Identifiants de connexion invalides."}, ensure_ascii=False).encode('utf-8'))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # ─── CLIENT KEY REQUESTS API ───
        elif self.path == '/api/client/request-key':
            try:
                payload = json.loads(post_data.decode('utf-8'))
                email = payload.get('userEmail', '').strip().lower()
                name = payload.get('name', '').strip()
                languages = payload.get('languages', [])
                quota = int(payload.get('quota', 1000))

                if not email or not name or not languages:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "error": "Champs invalides."}, ensure_ascii=False).encode('utf-8'))
                    return

                clients = load_clients()
                new_request = {
                    "id": str(int(datetime.utcnow().timestamp() * 1000)),
                    "name": name,
                    "apiKey": "",
                    "quota": quota,
                    "usage": 0,
                    "languages": languages,
                    "isActive": False,
                    "status": "pending",
                    "userEmail": email,
                    "created": datetime.utcnow().isoformat() + 'Z',
                    "lastUsed": "Jamais"
                }
                clients.append(new_request)
                save_clients(clients)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            except Exception as e:
                self.send_error(500, str(e))
            return

        # ─── CLIENT TRANSLATION API ───
        elif self.path == '/api/v1/translate':
            self.handle_client_translation(post_data)
            return

        self.send_response(404)
        self.end_headers()

    def serve_file(self, file_path, content_type):
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            print(f"Error serving static file {file_path}: {e}")
            try:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Erreur de lecture de fichier statique")
            except Exception:
                pass

    def handle_client_translation(self, post_data):
        # 1. API Key Auth
        api_key = self.headers.get('X-API-Key')
        if not api_key:
            auth_header = self.headers.get('Authorization', '')
            if auth_header.startswith('Bearer '):
                api_key = auth_header[7:]

        if not api_key:
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": "Authentification requise. Header 'X-API-Key' manquant."}, ensure_ascii=False).encode('utf-8'))
            return

        clients = load_clients()
        client = next((c for c in clients if c.get('apiKey') == api_key), None)

        if not client:
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": "Cle API invalide."}, ensure_ascii=False).encode('utf-8'))
            return

        # 2. Check client status
        if not client.get('isActive', True) or client.get('status') != 'active':
            self.send_response(403)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": "Cette cle API est désactivee ou suspendue."}, ensure_ascii=False).encode('utf-8'))
            return

        # 3. Parse body
        try:
            body = json.loads(post_data.decode('utf-8'))
        except Exception:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": "Format JSON invalide."}, ensure_ascii=False).encode('utf-8'))
            return

        text = body.get('text', '').strip()
        target_lang = body.get('target_lang', '').strip().lower()

        if not text or not target_lang:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": "Parametres manquants."}, ensure_ascii=False).encode('utf-8'))
            return

        # Language authorization check
        allowed_langs = client.get('languages', [])
        if allowed_langs and target_lang not in allowed_langs:
            self.send_response(403)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": f"Langue cible non autorisee pour cette cle. Langues autorisees : {allowed_langs}"}, ensure_ascii=False).encode('utf-8'))
            return

        # Quota check
        max_quota = int(client.get('quota', 1000))
        usage_count = int(client.get('usage', 0))
        if usage_count >= max_quota:
            self.send_response(429)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "error": "Quota d'appels API depasse pour cette cle."}, ensure_ascii=False).encode('utf-8'))
            return

        # 4. Process translation
        config = load_config()
        rules = config.get('rules', [])
        custom_dict = config.get('customDictionary', {})
        is_ai_enabled = config.get('isAiEnabled', False)
        
        # Languages configuration helper
        lang_names = {
            'moore': 'Mooré',
            'dioula': 'Dioula',
            'fulfulde': 'Fulfuldé',
            'gourounsi': 'Gourounsi',
            'bissa': 'Bissa'
        }
        target_lang_name = lang_names.get(target_lang, target_lang.capitalize())

        translation_result = None

        if is_ai_enabled:
            # Seed AI dictionary
            words = text.lower().split()
            merged_dict = {}
            if target_lang in dictionaries:
                merged_dict.update(dictionaries[target_lang])
            merged_dict.update(custom_dict.get(target_lang, {}))
            
            relevant_entries = {}
            for w in words:
                clean_w = re.sub(r'[.,!?;:]', '', w)
                if len(clean_w) > 2:
                    for k, v in merged_dict.items():
                        v_str = v.get("translation", "") if isinstance(v, dict) else str(v)
                        if clean_w in k or clean_w in v_str.lower():
                            if len(relevant_entries) < 30:
                                relevant_entries[k] = v

            relevant_rules = [r for r in rules if r.get('isActive', True) and r.get('language') == target_lang]
            custom_prompt = config.get('aiPromptTemplate')

            # Gemini
            gemini_key = config.get('geminiApiKey')
            if gemini_key:
                translation_result = call_gemini(
                    text=text,
                    target_lang_name=target_lang_name,
                    target_lang_key=target_lang,
                    api_key=gemini_key,
                    dict_subset=relevant_entries,
                    rules_subset=relevant_rules,
                    custom_prompt=custom_prompt
                )

            # OpenAI fallback
            if not translation_result:
                openai_key = config.get('openAiApiKey')
                if openai_key:
                    translation_result = call_openai(
                        text=text,
                        target_lang_name=target_lang_name,
                        target_lang_key=target_lang,
                        api_key=openai_key,
                        dict_subset=relevant_entries,
                        rules_subset=relevant_rules,
                        custom_prompt=custom_prompt
                    )

        # Local fallback
        if not translation_result:
            translation_result = local_translate(text, target_lang, rules, custom_dict)
            translation_result["ai_processed"] = False
        else:
            translation_result["ai_processed"] = True

        # Update stats
        client['usage'] = usage_count + 1
        client['lastUsed'] = datetime.utcnow().isoformat() + 'Z'
        save_clients(clients)

        # Return response
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        
        # Ensure syllables and vocal_reading are computed if local fallback
        syllables = translation_result.get("syllables", "")
        vocal_reading = translation_result.get("vocal_writing", "") or translation_result.get("vocal_reading", "")
        if not translation_result.get("ai_processed", False):
            if not syllables:
                syllables = self.compute_syllables(translation_result.get("translation", ""), target_lang, config)
            vocal_reading = self.compute_vocal_writing(translation_result.get("translation", ""), target_lang, config)

        response_body = {
            "success": True,
            "original_input": text,
            "corrected_input": translation_result.get("corrected_input", text),
            "translation": translation_result.get("translation", ""),
            "phonetic": translation_result.get("phonetic", ""),
            "syllables": syllables,
            "vocal_reading": vocal_reading,
            "rules_applied": translation_result.get("rules_applied", []),
            "ai_processed": translation_result.get("ai_processed", False),
            "target_lang": target_lang,
            "remaining_quota": max_quota - (usage_count + 1)
        }
        self.wfile.write(json.dumps(response_body, ensure_ascii=False).encode('utf-8'))

def run_server():
    server_address = ('', PORT)
    httpd = http.server.ThreadingHTTPServer(server_address, UnifiedHandler)
    print(f"Burkina Dict Unified Server running on http://localhost:{PORT}")
    print("Admin: http://localhost:8000/admin")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()

if __name__ == '__main__':
    run_server()
