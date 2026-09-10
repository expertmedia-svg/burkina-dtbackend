import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from enrich_references import extract
from enrich_smol import align
from translation_engine import TranslationEngine
from check_reference_data import check


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'reference_data').mkdir()
        self.ai = Mock(return_value={'translation': 'PROPOSITION'})
        self.online = Mock()
        self.online.lookup.return_value = ([], 'unavailable')
        self.config = {'isAiEnabled': True, 'externalResearchEnabled': True}

    def row(self, language='moore', local='LOCAL', gloss='recharger'):
        return {'id': 'wiki:test', 'language': language, 'local': local,
                'glosses': [gloss], 'definitions': [gloss], 'excerpt': gloss,
                'title': 'Source test', 'url': 'https://example.org/source',
                'license': 'CC BY-SA 4.0', 'validated': False}

    def engine(self, rows, dictionaries=None):
        (self.root / 'reference_data' / 'moore.jsonl').write_text(
            '\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        return TranslationEngine(dictionaries or {}, self.root / 'corpus.jsonl',
            self.root / 'proposals.json', self.ai, self.online)

    def test_exact_source_works_without_ai_but_is_not_validated(self):
        engine = self.engine([self.row()])
        result = engine.translate('recharger', 'fr', 'moore', {'isAiEnabled': False})
        self.assertEqual(result['translation'], 'LOCAL')
        self.assertEqual(result['source'], 'external_reference')
        self.assertEqual(result['validation_status'], 'pending_human_validation')
        self.assertEqual(result['sources'][0]['license'], 'CC BY-SA 4.0')
        self.ai.assert_not_called()

    def test_reference_context_survives_online_failure(self):
        engine = self.engine([self.row()])
        result = engine.translate('Je voudrais recharger.', 'fr', 'moore', self.config)
        context = self.ai.call_args.kwargs['dict_subset']
        self.assertEqual(len(context['documents']), 1)
        self.assertEqual(result['research_status'], 'available')
        self.assertEqual(result['source'], 'ai_with_references')

    def test_other_language_is_never_used(self):
        engine = self.engine([self.row(language='dioula')])
        engine.translate('Je voudrais recharger.', 'fr', 'moore', self.config)
        self.assertEqual(self.ai.call_args.kwargs['dict_subset']['documents'], [])

    def test_conflicting_unreviewed_dictionary_requires_context(self):
        engine = self.engine([self.row()], {'moore': {'recharger': {'translation': 'OTHER'}}})
        result = engine.translate('recharger', 'fr', 'moore', self.config)
        self.assertTrue(result['ai_processed'])
        self.assertEqual(len(self.ai.call_args.kwargs['dict_subset']['documents']), 1)

    def test_reviewed_dictionary_keeps_priority(self):
        engine = self.engine([self.row()], {'moore': {'recharger': {'translation': 'REVIEWED', 'validated': True}}})
        result = engine.translate('recharger', 'fr', 'moore', self.config)
        self.assertEqual(result['translation'], 'REVIEWED')
        self.ai.assert_not_called()

    def test_ambiguous_reference_is_not_an_exact_translation(self):
        other = {**self.row(local='OTHER'), 'id': 'wiki:other'}
        result = self.engine([self.row(), other]).translate('recharger', 'fr', 'moore', self.config)
        self.assertTrue(result['ai_processed'])

    def test_partial_gloss_extraction_cannot_hide_polysemy(self):
        row = self.row()
        row['definitions'].append('{{sens technique}} Autre sens')
        result = self.engine([row]).translate('LOCAL', 'moore', 'fr', self.config)
        self.assertTrue(result['ai_processed'])

    def test_import_excludes_wrong_language_and_keeps_revision(self):
        raw = '== {{langue|fr}} ==\n# FAUX\n== {{langue|mos}} ==\n# [[vrai]]\n== {{langue|dyu}} ==\n# AUTRE'
        page = {'title': 'LOCAL', 'revisions': [{'revid': 123, 'slots': {'main': {'content': raw}}}]}
        row = extract(page, 'moore')
        self.assertEqual(row['glosses'], ['vrai'])
        self.assertNotIn('FAUX', row['excerpt'])
        self.assertNotIn('AUTRE', row['excerpt'])
        self.assertTrue(row['url'].endswith('oldid=123'))
        self.assertIsNone(extract(page, 'fulfulde'))

    def test_pivot_is_context_only_and_preserves_alternatives(self):
        french = [{'src': 'charge', 'trgs': ['charge', 'prix']}]
        local = [{'src': 'charge', 'trgs': ['LOCAL1', 'LOCAL2']}]
        rows = align(french, local, 'moore', 'mos')
        self.assertEqual(rows[0]['french_hints'], ['charge', 'prix'])
        self.assertEqual(rows[0]['local_forms'], ['LOCAL1', 'LOCAL2'])
        self.assertEqual(rows[0]['glosses'], [])
        engine = self.engine(rows)
        result = engine.translate('charge', 'fr', 'moore', self.config)
        self.assertTrue(result['ai_processed'])
        self.assertEqual(self.ai.call_args.kwargs['dict_subset']['documents'][0]['kind'], 'pivot_lexicon')

    def test_reference_prompt_is_bounded(self):
        row = self.row()
        row['excerpt'] = 'x' * 100000
        row['definitions'] = ['recharger ' * 10000]
        documents = self.engine([row]).ranked_references('recharger', 'moore', False)
        self.assertLess(len(documents[0]['excerpt']), 2600)
        self.assertNotIn('definitions', documents[0])

    def test_distributed_snapshot_integrity_and_relevant_phone_retrieval(self):
        result = check()
        self.assertEqual(result['integrity'], 'ok')
        for language in ('moore', 'dioula'):
            titles = [doc['title'] for doc in result['retrieval_for_phone_sentence'][language]]
            self.assertTrue(any('phone (via anglais)' in title for title in titles))
            self.assertTrue(any('battery (via anglais)' in title for title in titles))
            self.assertFalse(any('my love' in title for title in titles))


if __name__ == '__main__':
    unittest.main()
