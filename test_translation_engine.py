import ast
import http.server
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from translation_engine import TranslationEngine, WiktionaryLookup, ranked_entries, normalize, ProposalStore
from review_translations import review


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Synthetic translations deliberately avoid asserting linguistic validity.
        self.dictionaries = {'moore': {
            'manger': {'translation': 'LOCAL_MANGER', 'validated': True, 'senses': 'se nourrir'},
            'bonjour': {'translation': 'LOCAL_SALUT', 'validated': True},
            'riz': {'translation': 'LOCAL_RIZ', 'validated': False},
            'erreur': {'translation': 'MAUVAIS', 'validated': False, 'source': 'ai_auto_enrichment'}}}
        self.ai = Mock(return_value={'translation': 'PROPOSITION', 'confidence': 1, 'validation_status': 'validated'})
        self.lookup = Mock()
        self.lookup.lookup.return_value = ([], 'no_match')
        self.engine = TranslationEngine(self.dictionaries, self.root / 'linguistic_corpus.jsonl',
            self.root / 'translation_proposals.json', self.ai, self.lookup)
        self.config = {'isAiEnabled': True, 'externalResearchEnabled': True}

    def test_validated_exact_needs_no_ai_or_network(self):
        result = self.engine.translate('Bonjour !', 'fr', 'moore', self.config)
        self.assertEqual(result['translation'], 'LOCAL_SALUT')
        self.assertEqual(result['validation_status'], 'validated')
        self.ai.assert_not_called()
        self.lookup.lookup.assert_not_called()

    def test_reverse_dictionary(self):
        result = self.engine.translate('LOCAL_SALUT', 'moore', 'fr', self.config)
        self.assertEqual(result['translation'], 'bonjour')

    def test_reverse_ai_receives_correct_direction_and_rules(self):
        self.config['rules'] = [{'language': 'moore', 'description': 'test', 'isActive': True},
                                {'language': 'dioula', 'description': 'other'}]
        self.engine.translate('LOCAL_PHRASE', 'moore', 'fr', self.config)
        args, kwargs = self.ai.call_args
        self.assertEqual(args[1:3], ('fr', 'moore'))
        self.assertEqual(len(kwargs['dict_subset']['rules']), 1)

    def test_retrieval_inflection_senses_and_unicode(self):
        self.assertIn('manger', ranked_entries('Nous mangeons', self.dictionaries['moore']))
        self.assertIn('manger', ranked_entries('nourrir', self.dictionaries['moore']))
        self.assertNotEqual(normalize('ã'), normalize('a'))

    def test_ai_score_cannot_validate_and_queue_does_not_pollute_dictionary(self):
        result = self.engine.translate('nouveau mot', 'fr', 'moore', self.config)
        self.assertEqual(result['validation_status'], 'pending_human_validation')
        self.assertIsNone(result['confidence'])
        self.assertNotIn('nouveau mot', self.dictionaries['moore'])
        self.assertIn(result['proposal_id'], json.loads((self.root / 'translation_proposals.json').read_text()))

    def test_old_generated_entries_are_excluded(self):
        result = self.engine.translate('erreur', 'fr', 'moore', {'isAiEnabled': False})
        self.assertEqual(result['translation'], '[erreur]')

    def test_partial_fallback_preserves_negation(self):
        self.ai.return_value = None
        result = self.engine.translate('ne pas manger', 'fr', 'moore', self.config)
        self.assertIn('[ne]', result['translation'])
        self.assertIn('[pas]', result['translation'])
        self.assertEqual(result['validation_status'], 'incomplete')

    def test_ambiguous_reverse_is_not_first_match(self):
        self.dictionaries['moore']['salut'] = {'translation': 'LOCAL_SALUT', 'validated': True}
        self.engine.translate('LOCAL_SALUT', 'moore', 'fr', self.config)
        self.ai.assert_called_once()

    def test_malformed_model_output_falls_back(self):
        for bad in [[], {}, {'translation': []}, {'translation': ''}]:
            self.ai.return_value = bad
            self.assertEqual(self.engine.translate('inconnu', 'fr', 'moore', self.config)['source'], 'local_rules_fallback')

    def test_source_provenance_only_from_retriever(self):
        doc = {'id': 'wiki:1', 'title': 'test', 'url': 'https://fr.wiktionary.org/w/index.php?oldid=1',
               'license': 'CC BY-SA', 'excerpt': 'test'}
        self.lookup.lookup.return_value = ([doc], 'available')
        self.ai.return_value = {'translation': 'LOCAL', 'sources': [{'url': 'https://invented.invalid'}]}
        result = self.engine.translate('nouveau', 'fr', 'moore', self.config)
        self.assertEqual(result['sources'][0]['url'], doc['url'])
        self.assertEqual(result['source'], 'ai_with_references')

    def test_outage_warns_but_model_can_propose(self):
        self.lookup.lookup.return_value = ([], 'unavailable')
        result = self.engine.translate('nouveau', 'fr', 'moore', self.config)
        self.assertIn('indisponible', result['warning'])

    def test_semantic_expansion_retrieves_without_changing_original(self):
        self.engine.query_planner = Mock(return_value=['manger'])
        self.engine.translate('Je souhaite me restaurer', 'fr', 'moore', self.config)
        args, kwargs = self.ai.call_args
        self.assertEqual(args[0], 'Je souhaite me restaurer')
        self.assertIn('manger', kwargs['dict_subset']['dictionary'])

    def test_brackets_override_model_claim_of_completeness(self):
        self.ai.return_value = {'translation': 'LOCAL [inconnu]', 'missing_terms': []}
        result = self.engine.translate('nouveau', 'fr', 'moore', self.config)
        self.assertEqual(result['validation_status'], 'incomplete')
        self.assertEqual(result['missing_terms'], ['inconnu'])

    def test_conditional_lemma_is_retrieved_without_claiming_translation(self):
        self.dictionaries['moore']['vouloir'] = {'translation': 'LOCAL_TEST', 'validated': False}
        self.ai.return_value = {'translation': 'PROPOSITION', 'missing_terms': ['voudrais', 'recharger']}
        result = self.engine.translate('Je voudrais recharger', 'fr', 'moore', self.config)
        args, kwargs = self.ai.call_args
        self.assertEqual(args[0], 'Je voudrais recharger')
        self.assertIn('vouloir', kwargs['dict_subset']['dictionary'])
        self.assertEqual(kwargs['dict_subset']['source_lemma_matches'], {'voudrais': 'vouloir'})
        self.assertEqual(result['dictionary_missing_terms'], ['recharger'])
        self.assertEqual(result['missing_terms'], ['voudrais', 'recharger'])
        self.assertEqual(result['validation_status'], 'incomplete')

    def test_review_promotes_only_after_explicit_human_approval(self):
        result = self.engine.translate('nouveau', 'fr', 'moore', self.config)
        review(self.root, result['proposal_id'], 'approve', 'Locuteur test', 'CORRIGE', 'Variété test')
        self.ai.reset_mock()
        result = self.engine.translate('nouveau', 'fr', 'moore', self.config)
        self.assertEqual(result['translation'], 'CORRIGE')
        self.assertEqual(result['source'], 'validated_corpus')
        self.ai.assert_not_called()
        self.assertEqual(self.engine.translate('CORRIGE', 'moore', 'fr', self.config)['translation'], 'nouveau')

    def test_unreviewed_corpus_is_not_used(self):
        (self.root / 'linguistic_corpus.jsonl').write_text(json.dumps({'language': 'moore',
            'french': 'nouveau', 'local': 'FAUX', 'validated': False}) + '\n')
        self.assertEqual(self.engine.translate('nouveau', 'fr', 'moore', self.config)['translation'], 'PROPOSITION')

    def test_direction_and_length_validation(self):
        for source, target, text in [('fr', 'fr', 'x'), ('moore', 'dioula', 'x'), ('fr', 'moore', 'x' * 3001)]:
            with self.assertRaises(ValueError):
                self.engine.translate(text, source, target, self.config)


class LookupTests(unittest.TestCase):
    def test_language_filter_and_sense(self):
        text = '''== {{langue|fr}} ==
{{trad-début|Sens précis}}
* {{T|mos}} : {{trad+|mos|LOCAL}}
* {{T|bm}} : {{trad+|bm|AUTRE}}
== {{langue|en}} ==
* {{trad+|mos|WRONG_SECTION}}
'''
        result = WiktionaryLookup.extract(text, 'moore', False)
        self.assertIn('Sens précis', result)
        self.assertIn('LOCAL', result)
        self.assertNotIn('AUTRE', result)
        self.assertNotIn('WRONG_SECTION', result)

    def test_reverse_language_section(self):
        text = '== {{langue|ff}} ==\n# [[bonjour]].\n== {{langue|fr}} ==\n# Mauvais.'
        self.assertIn('bonjour', WiktionaryLookup.extract(text, 'fulfulde', True))
        self.assertNotIn('Mauvais', WiktionaryLookup.extract(text, 'fulfulde', True))

    def test_real_api_schema_and_cache(self):
        data = {'query': {'pages': [{'title': 'bonjour', 'revisions': [{'revid': 12,
             'slots': {'main': {'content': '== {{langue|fr}} ==\n* {{trad|dyu|LOCAL}}'}}}]}]}}
        opener = Mock(side_effect=lambda *a, **k: io.BytesIO(json.dumps(data).encode()))
        lookup = WiktionaryLookup(opener)
        docs, status = lookup.lookup(['bonjour'], 'dioula')
        self.assertEqual(status, 'available')
        self.assertIn('oldid=12', docs[0]['url'])
        lookup.lookup(['bonjour'], 'dioula')
        opener.assert_called_once()

    def test_network_failure(self):
        lookup = WiktionaryLookup(Mock(side_effect=TimeoutError()))
        self.assertEqual(lookup.lookup(['bonjour'], 'moore'), ([], 'unavailable'))


class RouteTests(unittest.TestCase):
    """Exercise production handler without importing server startup/key loading."""
    def setUp(self):
        tree = ast.parse(Path(__file__).with_name('server.py').read_text(encoding='utf-8'))
        handler = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'UnifiedHandler')
        self.engine = Mock()
        self.engine.translate.return_value = {'success': True, 'translation': 'bonjour',
                                             'validation_status': 'validated'}
        scope = {'http': __import__('http'), 'json': json, 'os': __import__('os'),
                 'SUPPORTED_LANGUAGES': {'moore': 'Mooré'}, 'load_config': lambda: {},
                 'translation_engine': self.engine}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), 'server.py', 'exec'), scope)
        self.handler = object.__new__(scope['UnifiedHandler'])
        self.handler.wfile = io.BytesIO()
        self.handler.send_response = Mock()
        self.handler.send_header = Mock()
        self.handler.end_headers = Mock()
        self.handler.authenticate_client = Mock(return_value=({}, None))

    def test_reverse_route_authorizes_local_language_and_calls_engine(self):
        self.handler.handle_extended_api('/api/v1/translate-word', json.dumps(
            {'text': 'LOCAL', 'source_lang': 'moore', 'target_lang': 'fr'}).encode())
        self.handler.authenticate_client.assert_called_once_with('moore')
        self.assertEqual(self.engine.translate.call_args.args[:3], ('LOCAL', 'moore', 'fr'))
        self.assertEqual(json.loads(self.handler.wfile.getvalue())['validation_status'], 'validated')

    def test_invalid_request_does_not_charge_quota(self):
        self.handler.handle_extended_api('/translate-sentence', b'{"text": [], "target_lang": "moore"}')
        self.handler.send_response.assert_called_with(400)
        self.handler.authenticate_client.assert_not_called()

    def test_legacy_route_uses_common_engine(self):
        self.handler.handle_client_translation(b'{"text": "hello", "target_lang": "moore"}')
        self.engine.translate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
