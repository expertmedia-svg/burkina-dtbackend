import csv
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from expert_api import ApiError, ExpertStore, handle_expert
from translation_engine import TranslationEngine


class ExpertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ExpertStore(self.root / 'expert_data')
        self.expert = {'username': 'expert', 'role': 'expert', 'languages': ['moore']}
        self.reviewer = {**self.expert, 'username': 'reviewer', 'role': 'reviewer'}

    def make(self, kind='sentence', **data):
        return self.store.save({'kind': kind, 'language': 'moore', 'submit': True,
            'data': {'french': 'Bonjour test', 'local': 'LOCAL_TEST', 'dialect': 'Variété test', **data}}, self.expert)

    def approve(self, task):
        return self.store.review(task['id'], {'decision': 'approved', 'version': task['version']}, self.reviewer)

    def import_audio(self):
        root = self.root / 'tts'
        manifest = root / 'data/manifests/moore_segments.csv'
        audio = root / 'data/processed/moore/audio/a.wav'
        audio.parent.mkdir(parents=True); audio.write_bytes(bytes(range(100)))
        manifest.parent.mkdir(parents=True)
        with manifest.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['segment_id','audio_file','text','duration_seconds','source_file'])
            writer.writeheader(); writer.writerow({'segment_id':'a','audio_file':audio.relative_to(root).as_posix(),
                'text':'Texte ancien français', 'duration_seconds':5,'source_file':'original.mpeg'})
        self.assertEqual(self.store.import_segments(root), 1)
        self.assertEqual(self.store.import_segments(root), 0)
        return self.store.task('audio_moore_a', self.expert)

    def test_password_sessions_and_revocation(self):
        self.store.create_user('expert', 'a-password-long-enough', 'expert', ['moore'])
        with self.assertRaises(ApiError): self.store.login('expert', 'incorrect')
        login = self.store.login('expert', 'a-password-long-enough')
        self.assertEqual(self.store.user(login['token'])['role'], 'expert')
        self.assertNotIn('password', login['user'])
        with self.store.connect() as db:
            self.assertNotEqual(db.execute('SELECT token FROM sessions').fetchone()[0], login['token'])
        self.store.create_user('expert', 'new-password-long-enough', 'expert', ['dioula'])
        with self.assertRaises(ApiError): self.store.user(login['token'])

    def test_expert_cannot_validate_or_access_other_language(self):
        task = self.make()
        with self.assertRaises(ApiError) as error:
            self.store.review(task['id'], {'decision':'approved','version':task['version']}, self.expert)
        self.assertEqual(error.exception.status, 403)
        with self.assertRaises(ApiError):
            self.store.task(task['id'], {**self.expert, 'languages':['dioula']})

    def test_concurrent_edits_cannot_overwrite_and_are_audited(self):
        task = self.make()
        request = {**task, 'data': {**task['data'], 'local':'CORRECTED'}}
        saved = self.store.save(request, self.expert)
        with self.assertRaises(ApiError) as error: self.store.save(request, self.expert)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(saved['version'], 2)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 2)

    def test_only_approved_data_reaches_translation_engine_and_edit_withdraws_it(self):
        task = self.make()
        engine = TranslationEngine({}, self.root/'corpus.jsonl', self.root/'proposals.json', lambda *a, **k: None)
        before = engine.translate('Bonjour test', 'fr', 'moore', {'isAiEnabled':False})
        self.assertNotEqual(before['validation_status'], 'validated')
        approved = self.approve(task)
        result = engine.translate('Bonjour test', 'fr', 'moore', {'isAiEnabled':False})
        self.assertEqual(result['translation'], 'LOCAL_TEST')
        self.assertEqual(result['validation_status'], 'validated')
        self.store.save(approved, self.expert)
        self.assertNotEqual(engine.translate('Bonjour test', 'fr', 'moore', {'isAiEnabled':False})['validation_status'], 'validated')

    def test_word_and_spelling_are_published_without_altering_originals(self):
        self.approve(self.make('word'))
        self.approve(self.make('spelling', original='bonjor test', local='Bonjour test', spelling_side='fr'))
        dictionaries = {'moore': {'Bonjour test': {'translation':'OLD'}}}
        engine = TranslationEngine(dictionaries, self.root/'corpus.jsonl', self.root/'proposals.json', lambda *a, **k: None)
        result = engine.translate('bonjor test', 'fr', 'moore', {'isAiEnabled':False})
        self.assertEqual(result['translation'], 'LOCAL_TEST')
        self.assertEqual(result['original_input'], 'bonjor test')
        self.assertEqual(result['corrected_input'], 'Bonjour test')
        self.assertEqual(dictionaries['moore']['Bonjour test']['translation'], 'OLD')

    def test_incomplete_translation_cannot_be_approved(self):
        task = self.make(local='[missing]')
        with self.assertRaises(ApiError): self.approve(task)

    def test_import_preserves_uncertain_text_and_export_excludes_unconsented_audio(self):
        task = self.import_audio()
        self.assertEqual(task['data']['local'], '')
        self.assertEqual(task['data']['imported_text'], 'Texte ancien français')
        task['data'].update(local='LOCAL', french='FR', dialect='Test', speaker_id='speaker1')
        task['submit'] = True
        approved = self.approve(self.store.save(task, self.expert))
        report = self.store.export(self.root/'export1')
        self.assertEqual(report['speech_segments'], 0)
        self.assertEqual(len(report['skipped_audio_without_consent_or_speaker']), 1)
        approved['data']['consent_ref'] = 'autorisation-test'
        approved['submit'] = True
        self.approve(self.store.save(approved, self.expert))
        report = self.store.export(self.root/'export2')
        self.assertEqual(report['speech_segments'], 1)
        speech = json.loads((self.root/'export2/speech.jsonl').read_text())
        translation = json.loads((self.root/'export2/translation.jsonl').read_text())
        self.assertEqual(speech['split'], translation['split'])
        self.assertEqual(speech['text'], 'LOCAL')

    def test_word_document_keeps_paragraphs_and_unicode(self):
        path = self.root/'source.docx'
        xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Mooré ɛ</w:t></w:r></w:p><w:p><w:r><w:t>Français</w:t></w:r></w:p></w:body></w:document>'
        with zipfile.ZipFile(path, 'w') as archive: archive.writestr('word/document.xml', xml)
        identifier = self.store.import_docx(path, 'moore', 'original.mpeg')
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM documents WHERE id=?', (identifier,)).fetchone()
        self.assertEqual(row['text'], 'Mooré ɛ\nFrançais')
        self.assertEqual(row['source_file'], 'original.mpeg')

    def test_http_authentication_and_audio_ranges(self):
        self.import_audio()
        self.store.create_user('expert', 'a-password-long-enough', 'expert', ['moore'])
        token = self.store.login('expert', 'a-password-long-enough')['token']
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self): handle_expert(self)
            def do_POST(self): handle_expert(self)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_port}/expert-api/audio/audio_moore_a'
        try:
            with patch.dict('os.environ', {'EXPERT_DATA_DIR':str(self.store.root)}):
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(url)
                self.assertEqual(error.exception.code, 401)
                request = urllib.request.Request(url, headers={'Authorization':'Bearer '+token,'Range':'bytes=10-19'})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), bytes(range(10,20)))
                    self.assertEqual(response.headers['Content-Range'], 'bytes 10-19/100')
                request = urllib.request.Request(url, headers={'Authorization':'Bearer '+token,'Range':'bytes=200-300'})
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 416)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
