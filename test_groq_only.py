import ast
import io
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.request
from translation_engine import merged_dictionary, ranked_entries
from groq_transport import groq_json_request, completion_budget, record_groq_error


class GroqOnlyTests(unittest.TestCase):
    def test_http_diagnostic_keeps_cause_and_redacts_credentials(self):
        from smoke_translation import safe_http_diagnostic
        from urllib.error import HTTPError
        body = json.dumps({'error': {'code': 'invalid_api_key', 'message':
            'Rejected secret-value gsk_anothersecret Bearer token-value', 'failed_generation': 'private'}}).encode()
        result = safe_http_diagnostic(HTTPError('https://api.groq.com/', 401, 'Unauthorized', {}, io.BytesIO(body)), 'secret-value')
        self.assertEqual(result['http_status'], 401)
        self.assertEqual(result['code'], 'invalid_api_key')
        self.assertNotIn('secret-value', result['message'])
        self.assertNotIn('gsk_', result['message'])
        self.assertNotIn('token-value', result['message'])
        self.assertNotIn('failed_generation', result)

    def test_all_ai_functions_use_groq_and_chat_language_is_preserved(self):
        source = Path(__file__).with_name('server.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith('call_')]
        self.assertEqual({n.name for n in functions},
                         {'call_ai_retrieval_plan', 'call_ai_rich_translation', 'call_ai_conversation'})
        scope = {'json': json, 'urllib': __import__('urllib'), 'dictionaries': {},
                 'merged_dictionary': merged_dictionary, 'ranked_entries': ranked_entries,
                 'groq_json_request': groq_json_request, 'completion_budget': completion_budget,
                 'record_groq_error': record_groq_error}
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'server.py', 'exec'), scope)
        requests = []
        def reply(request, **kwargs):
            requests.append(request)
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{"translation":"test","terms":[],"response_text":"test"}'}}]}).encode())
        config = {'groqApiKey': 'test-only', 'responseLanguage': 'fr'}
        with patch('urllib.request.urlopen', side_effect=reply):
            scope['call_ai_retrieval_plan']('test', 'fr', config)
            scope['call_ai_rich_translation']('test', 'moore', 'fr', 'Mooré', config)
            scope['call_ai_conversation']('test', 'moore', 'Mooré', [], config)
        self.assertEqual(len(requests), 3)
        for request in requests:
            self.assertTrue(request.full_url.startswith('https://api.groq.com/'))
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-only')
            self.assertEqual(request.get_header('User-agent'), 'BurkinaDict/1.1')
            self.assertLessEqual(json.loads(request.data)['max_completion_tokens'], 512)
        self.assertIn('Langue obligatoire de response_text : fr', json.loads(requests[-1].data)['messages'][0]['content'])
        requests.clear()
        config['groqModel'] = 'qwen/qwen3.6-27b'
        with patch('urllib.request.urlopen', side_effect=reply):
            self.assertEqual(scope['call_ai_retrieval_plan']('test', 'fr', config), [])
            scope['call_ai_rich_translation']('test', 'moore', 'fr', 'Mooré', config)
        self.assertEqual(len(requests), 1)
        self.assertEqual(json.loads(requests[0].data)['reasoning_effort'], 'none')

    @unittest.skipUnless(shutil.which('node'), 'Node requis pour vérifier JavaScript')
    def test_admin_javascript_syntax_and_single_provider(self):
        html = (Path(__file__).parent / 'frontend/admin/espace_professeur.html').read_text(encoding='utf-8')
        scripts = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', html, re.S)
        result = subprocess.run(['node', '--check'], input='\n'.join(scripts), text=True,
                                capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('groq-key', html)
        for obsolete in ('gemini-key', 'openai-key', 'eleven-key'):
            self.assertNotIn(obsolete, html)


if __name__ == '__main__':
    unittest.main()
