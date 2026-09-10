"""Live technical smoke check, without loading the server or modifying dictionaries.

Uses configured Groq credentials without printing them. Proposals go to a temp
directory. This checks integration, not linguistic accuracy.
"""
import ast
import argparse
from groq_transport import groq_json_request, completion_budget, record_groq_error, describe_http_error
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from unittest.mock import patch
from pathlib import Path
from translation_engine import TranslationEngine, read_json, LANGUAGES


def safe_http_diagnostic(error, api_key):
    """Keep provider error metadata, never credentials or generated content."""
    return describe_http_error(error, api_key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=list(LANGUAGES), default='moore')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    config = read_json(directory / 'academy_config.json', {})
    env = dict(os.environ)
    if (directory / '.env').exists():
        for line in (directory / '.env').read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    config['groqApiKey'] = env.get('GROQ_API_KEY') or config.get('groqApiKey')
    config['groqModel'] = env.get('GROQ_MODEL') or config.get('groqModel')
    if not config.get('groqApiKey'):
        print('Groq non configuré : test réel non exécuté.')
        return
    config['groqMaxCompletionTokens'] = env.get('GROQ_MAX_COMPLETION_TOKENS', 512)
    print('Modèle testé : ' + str(config.get('groqModel') or 'openai/gpt-oss-120b'), flush=True)
    config.update(isAiEnabled=True, externalResearchEnabled=True)
    tree = ast.parse((directory / 'server.py').read_text(encoding='utf-8'))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('call_ai_retrieval_plan', 'call_ai_rich_translation')]
    scope = {'json': json, 'urllib': __import__('urllib'), 'groq_json_request': groq_json_request, 'completion_budget': completion_budget, 'record_groq_error': record_groq_error}
    exec(compile(ast.Module(body=functions, type_ignores=[]), 'server.py', 'exec'), scope)
    dictionaries = {}
    for lang in LANGUAGES:
        path = directory.parent / f'dictionnaire_{lang}_1000.csv'
        if not path.exists():
            path = directory / path.name
        dictionaries[lang] = read_json(path, {})
    with tempfile.TemporaryDirectory() as temp:
        engine = TranslationEngine(dictionaries, directory / 'linguistic_corpus.jsonl',
            Path(temp) / 'proposals.json', scope['call_ai_rich_translation'],
            query_planner=scope['call_ai_retrieval_plan'])
        lang = args.language
        result = engine.translate('Je voudrais recharger la batterie de mon téléphone.', 'fr', lang, config)
        print(json.dumps({'language': lang, 'ai_processed': result['ai_processed'],
            'source': result['source'], 'research_status': result.get('research_status'),
            'validation_status': result['validation_status'], 'nonempty': bool(result['translation'])}), flush=True)
        if config.get('groqLastError'):
            print('Diagnostic conservé : ' + json.dumps(config['groqLastError'], ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
