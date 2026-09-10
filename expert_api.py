"""Authenticated expert workspace, mounted at /expert-api by server.py.

SQLite stores revisions and review history. Only explicitly approved work is
published to expert_validated.json for the translation engine.
"""
import argparse
import csv
from contextlib import contextmanager
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

LANGUAGES = ('moore', 'dioula', 'fulfulde')
KINDS = ('audio', 'word', 'sentence', 'spelling')
WRITE_LOCK = threading.RLock()


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260000).hex()


class ExpertStore:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'experts.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, salt TEXT NOT NULL,
                    password TEXT NOT NULL, role TEXT NOT NULL, languages TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, username TEXT NOT NULL,
                    expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    language TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
                    updated REAL NOT NULL, author TEXT NOT NULL, reviewer TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, task_id TEXT NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, at REAL NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, language TEXT NOT NULL,
                    name TEXT NOT NULL, text TEXT NOT NULL, source_file TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS tasks_filter ON tasks(language, status, kind, updated);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_user(self, username, password, role, languages):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', username) or len(password) < 12:
            raise ApiError('Identifiant : 3–64 caractères ; mot de passe : au moins 12 caractères.')
        if role not in ('expert', 'reviewer') or not languages or not set(languages) <= set(LANGUAGES):
            raise ApiError('Rôle ou langues invalides.')
        salt = secrets.token_hex(16)
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO users VALUES(?,?,?,?,?)',
                       (username, salt, password_hash(password, salt), role, json.dumps(languages)))
            db.execute('DELETE FROM sessions WHERE username=?', (username,))

    def login(self, username, password):
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            actual = password_hash(password, row['salt'] if row else 'unknown')
            if not row or not hmac.compare_digest(actual, row['password']):
                raise ApiError('Identifiant ou mot de passe incorrect.', 401)
            token = secrets.token_urlsafe(40)
            db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), username, time.time() + 12 * 3600))
            return {'token': token, 'user': self.public_user(row)}

    @staticmethod
    def public_user(row):
        return {'username': row['username'], 'role': row['role'], 'languages': json.loads(row['languages'])}

    def user(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute('SELECT u.* FROM users u JOIN sessions s ON s.username=u.username '
                             'WHERE s.token=? AND s.expires>?', (digest, time.time())).fetchone()
        if not row:
            raise ApiError('Session expirée. Reconnectez-vous.', 401)
        return self.public_user(row)

    @staticmethod
    def allowed(user, language):
        if language not in user['languages']:
            raise ApiError('Cette langue ne vous est pas attribuée.', 403)

    @staticmethod
    def unpack(row):
        return {**dict(row), 'data': json.loads(row['data'])}

    def task(self, identifier, user):
        with self.connect() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ApiError('Travail introuvable.', 404)
        self.allowed(user, row['language'])
        return self.unpack(row)

    def listing(self, user, query):
        language = query.get('language', [user['languages'][0]])[0]
        self.allowed(user, language)
        where, values = ['language=?'], [language]
        for key in ('kind', 'status'):
            value = query.get(key, [''])[0]
            if value:
                where.append(key + '=?'); values.append(value)
        search = query.get('q', [''])[0][:100]
        if search:
            where.append('(data LIKE ? OR id LIKE ?)'); values.extend(['%' + search + '%'] * 2)
        offset = max(0, min(1000000, int(query.get('offset', ['0'])[0])))
        clause = ' AND '.join(where)
        with self.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM tasks WHERE ' + clause, values).fetchone()[0]
            rows = db.execute('SELECT * FROM tasks WHERE ' + clause + ' ORDER BY updated DESC,id LIMIT 40 OFFSET ?',
                              [*values, offset]).fetchall()
            stats = db.execute('SELECT kind,status,COUNT(*) AS count FROM tasks WHERE language=? GROUP BY kind,status',
                               (language,)).fetchall()
        return {'items': [self.unpack(row) for row in rows], 'total': count, 'stats': [dict(r) for r in stats]}

    def save(self, payload, user):
        identifier = str(payload.get('id') or uuid.uuid4().hex)
        language, kind = payload.get('language'), payload.get('kind')
        self.allowed(user, language)
        if kind not in KINDS:
            raise ApiError('Type de travail invalide.')
        fields = ('french', 'local', 'original', 'spelling_side', 'dialect', 'speaker_id',
                  'consent_ref', 'notes', 'source')
        incoming = payload.get('data', {})
        if not isinstance(incoming, dict) or any(not isinstance(incoming.get(k, ''), str) for k in fields):
            raise ApiError('Champs textuels invalides.')
        if any(len(incoming.get(k, '')) > 10000 for k in fields):
            raise ApiError('Texte trop long.')
        status = 'pending' if payload.get('submit') is True else 'draft'
        with WRITE_LOCK, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM tasks WHERE id=?', (identifier,)).fetchone()
            if old:
                self.allowed(user, old['language'])
                if old['kind'] != kind or old['language'] != language:
                    raise ApiError('Le type et la langue ne peuvent pas changer.')
                if payload.get('version') != old['version']:
                    raise ApiError('Un autre expert a modifié ce travail. Rechargez-le avant de corriger.', 409)
                data = json.loads(old['data'])
            else:
                if kind == 'audio':
                    raise ApiError('Les segments audio doivent être importés depuis un manifeste.')
                data = {}
            data.update({k: incoming.get(k, data.get(k, '')).strip() for k in fields})
            if data.get('spelling_side') not in ('', 'fr', 'local'):
                raise ApiError('Langue de correction invalide.')
            version = old['version'] + 1 if old else 1
            db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',
                       (identifier, kind, language, status, version, time.time(), user['username'], '', json.dumps(data, ensure_ascii=False)))
            db.execute('INSERT INTO audit(task_id,actor,action,at,data) VALUES(?,?,?,?,?)',
                       (identifier, user['username'], status, time.time(), json.dumps(data, ensure_ascii=False)))
        self.publish()
        return self.task(identifier, user)

    def review(self, identifier, payload, user):
        if user['role'] != 'reviewer':
            raise ApiError('Validation réservée aux relecteurs.', 403)
        decision = payload.get('decision')
        if decision not in ('approved', 'rejected'):
            raise ApiError('Décision invalide.')
        with WRITE_LOCK, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tasks WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise ApiError('Travail introuvable.', 404)
            self.allowed(user, row['language'])
            if row['version'] != payload.get('version') or row['status'] != 'pending':
                raise ApiError('Le travail a changé ou doit d’abord être soumis.', 409)
            data = json.loads(row['data'])
            if decision == 'approved':
                required = ('original', 'local', 'dialect') if row['kind'] == 'spelling' else ('french', 'local', 'dialect')
                if any(not data.get(k, '').strip() for k in required):
                    raise ApiError('Complétez les textes et la variété linguistique avant validation.')
                if '[' in data['local'] or ']' in data['local']:
                    raise ApiError('La transcription/traduction contient encore des passages manquants.')
                if row['kind'] == 'audio' and data.get('speaker_id', '') in ('', 'A_COMPLETER'):
                    raise ApiError('Identifiez le locuteur pour ce segment.')
            db.execute('UPDATE tasks SET status=?,reviewer=?,version=version+1,updated=? WHERE id=?',
                       (decision, user['username'], time.time(), identifier))
            db.execute('INSERT INTO audit(task_id,actor,action,at,data) VALUES(?,?,?,?,?)',
                       (identifier, user['username'], decision, time.time(), json.dumps(data, ensure_ascii=False)))
        self.publish()
        return self.task(identifier, user)

    def publish(self):
        # Also coordinates separate importer/reviewer processes.
        from translation_engine import proposal_lock
        with proposal_lock(self.root / 'publish'), self.connect() as db:
            rows = [self.unpack(r) for r in db.execute("SELECT * FROM tasks WHERE status='approved' ORDER BY updated")]
            snapshot = {'dictionary': {lang: {} for lang in LANGUAGES}, 'corpus': [], 'spelling': []}
            words = {}
            for row in rows:
                data = row['data']
                common = {'id': row['id'], 'language': row['language'], 'reviewer': row['reviewer'],
                          'validated': True, 'dialect': data['dialect'], 'source': 'Expert : ' + row['reviewer']}
                if row['kind'] == 'spelling':
                    snapshot['spelling'].append({**common, 'original': data['original'],
                        'corrected': data['local'], 'side': data.get('spelling_side') or 'local'})
                elif row['kind'] == 'word':
                    words.setdefault((row['language'], data['french']), []).append({**common, 'translation': data['local']})
                else:
                    snapshot['corpus'].append({**common, 'french': data['french'], 'local': data['local'],
                                               'references': data.get('references', [])})
            for (lang, french), entries in words.items():
                if len({e['translation'] for e in entries}) == 1:
                    snapshot['dictionary'][lang][french] = entries[-1]
            temporary = self.root / 'expert_validated.tmp'
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.root / 'expert_validated.json')

    def import_segments(self, tts_root):
        root = Path(tts_root).resolve()
        audio_dir = self.root / 'audio'
        audio_dir.mkdir(exist_ok=True)
        count = 0
        with WRITE_LOCK, self.connect() as db:
            for language in LANGUAGES:
                manifest = root / 'data' / 'manifests' / (language + '_segments.csv')
                if not manifest.exists():
                    continue
                with manifest.open(encoding='utf-8-sig', newline='') as stream:
                    for row in csv.DictReader(stream):
                        identifier = 'audio_' + language + '_' + row['segment_id']
                        if not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', identifier):
                            raise ApiError('Identifiant de segment invalide.')
                        if db.execute('SELECT 1 FROM tasks WHERE id=?', (identifier,)).fetchone():
                            continue
                        audio = (root / row['audio_file']).resolve()
                        if root / 'data' / 'processed' not in audio.parents or audio.suffix.lower() != '.wav':
                            raise ApiError('Le segment doit être un WAV dans data/processed.')
                        shutil.copy2(audio, audio_dir / (identifier + '.wav'))
                        data = {'audio_file': 'audio/' + identifier + '.wav',
                            'source_file': row.get('source_file', ''), 'source_segment_id': row['segment_id'],
                            'duration_seconds': float(row.get('duration_seconds') or 0),
                            'start_seconds': float(row.get('start_seconds') or 0),
                            'end_seconds': float(row.get('end_seconds') or 0),
                            'local': row.get('text_local', ''), 'french': row.get('text_fr', row.get('translation_fr', '')),
                            'speaker_id': row.get('speaker_id', ''), 'dialect': row.get('dialect', ''),
                            'consent_ref': row.get('consent_ref', ''),
                            'imported_text': row.get('text', ''),
                            'notes': 'Texte historique à vérifier et répartir entre transcription locale et français.'}
                        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',
                            (identifier, 'audio', language, 'draft', 1, time.time(), 'import', '', json.dumps(data, ensure_ascii=False)))
                        count += 1
        return count

    def import_docx(self, filename, language, source_file=''):
        if language not in LANGUAGES:
            raise ApiError('Langue invalide.')
        path = Path(filename)
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo('word/document.xml').file_size > 10000000:
                raise ApiError('Document trop volumineux.')
            document = ET.fromstring(archive.read('word/document.xml'))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        text = '\n'.join(''.join(t.text or '' for t in p.findall('.//w:t', ns))
                         for p in document.findall('.//w:p', ns))
        identifier = hashlib.sha256((language + source_file + text).encode()).hexdigest()[:24]
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?)',
                       (identifier, language, path.name, text, source_file))
        return identifier

    def import_dictionary(self, directory):
        count = 0
        with self.connect() as db:
            for language in LANGUAGES:
                path = Path(directory) / ('dictionnaire_' + language + '_1000.csv')
                if not path.exists():
                    continue
                entries = json.loads(path.read_text(encoding='utf-8-sig'))
                for french, entry in entries.items():
                    translation = entry.get('translation', '') if isinstance(entry, dict) else str(entry)
                    identifier = 'word_' + hashlib.sha256((language + ':' + french).encode()).hexdigest()[:24]
                    data = {'french': french, 'local': translation, 'dialect': '',
                            'source': path.name, 'notes': 'Entrée historique à relire, non validée dans cet atelier.'}
                    cursor = db.execute('INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',
                        (identifier, 'word', language, 'draft', 1, time.time(), 'import', '', json.dumps(data, ensure_ascii=False)))
                    count += cursor.rowcount
        return count

    def import_proposals(self, filename):
        count = 0
        proposals = json.loads(Path(filename).read_text(encoding='utf-8'))
        with self.connect() as db:
            for identifier, row in proposals.items():
                if row.get('status') != 'pending_human_validation':
                    continue
                reverse = row.get('target_lang') == 'fr'
                language = row.get('source_lang') if reverse else row.get('target_lang')
                if language not in LANGUAGES:
                    continue
                data = {'french': row.get('translation' if reverse else 'text', ''),
                    'local': row.get('text' if reverse else 'translation', ''), 'dialect': '',
                    'source': 'Proposition IA ' + identifier, 'references': row.get('sources', []),
                    'notes': 'Proposition IA : corriger et préciser la variété avant validation.'}
                key = 'proposal_' + hashlib.sha256(identifier.encode()).hexdigest()[:24]
                cursor = db.execute('INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',
                    (key, 'sentence', language, 'draft', 1, time.time(), 'import', '', json.dumps(data, ensure_ascii=False)))
                count += cursor.rowcount
        return count

    def export(self, destination):
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            rows = [self.unpack(r) for r in db.execute("SELECT * FROM tasks WHERE status='approved'")]
        translation, speech, skipped = [], [], []
        for row in rows:
            data = row['data']
            if row['kind'] in ('word', 'sentence', 'audio'):
                group = data.get('speaker_id') if row['kind'] == 'audio' else data['french'].strip().casefold()
                group = group or row['id']
                bucket = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 100
                translation.append({'id': row['id'], 'language': row['language'],
                    'french': data['french'], 'local': data['local'], 'reviewer': row['reviewer'], 'dialect': data['dialect']})
                translation[-1]['split'] = 'test' if bucket < 5 else 'validation' if bucket < 10 else 'train'
            if row['kind'] != 'audio':
                continue
            if not data.get('consent_ref') or data.get('speaker_id') in ('', None, 'A_COMPLETER'):
                skipped.append(row['id']); continue
            source = (self.root / data['audio_file']).resolve()
            if self.root / 'audio' not in source.parents:
                raise ApiError('Chemin audio invalide.')
            target = destination / data['audio_file']
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
            # A speaker always receives the same partition, across both languages.
            bucket = int(hashlib.sha256(data['speaker_id'].encode()).hexdigest()[:8], 16) % 100
            split = 'test' if bucket < 5 else 'validation' if bucket < 10 else 'train'
            speech.append({'id': row['id'], 'language': row['language'], 'audio': data['audio_file'],
                'text': data['local'], 'translation_fr': data['french'], 'speaker_id': data['speaker_id'],
                'consent_ref': data['consent_ref'], 'duration_seconds': data['duration_seconds'],
                'source_file': data['source_file'], 'split': split, 'reviewer': row['reviewer']})
        for name, records in [('translation.jsonl', translation), ('speech.jsonl', speech)]:
            (destination / name).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
        report = {'translation_pairs': len(translation), 'speech_segments': len(speech),
            'speech_hours': sum(r['duration_seconds'] for r in speech) / 3600,
            'skipped_audio_without_consent_or_speaker': skipped,
            'partitions': {s: sum(r['split'] == s for r in speech) for s in ('train', 'validation', 'test')},
            'note': 'Corpus exporté, aucun modèle entraîné. Vérifier diversité des locuteurs et partitions non vides.'}
        (destination / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return report


_stores = {}
_login_attempts = {}


def handle_expert(handler):
    parsed = urlsplit(handler.path)
    if not parsed.path.startswith('/expert-api/'):
        return False
    directory = Path(os.environ.get('EXPERT_DATA_DIR') or Path(__file__).parent / 'expert_data')
    with WRITE_LOCK:
        store = _stores.setdefault(str(directory), None)
        if store is None:
            store = _stores[str(directory)] = ExpertStore(directory)
    def send(data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode()
        handler.send_response(status)
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.send_header('Content-Length', str(len(raw)))
        handler.send_header('Cache-Control', 'no-store')
        handler.end_headers(); handler.wfile.write(raw)
    try:
        payload = {}
        if handler.command == 'POST':
            length = int(handler.headers.get('Content-Length', '0'))
            if not 0 < length <= 100000:
                raise ApiError('Requête trop volumineuse ou vide.', 413)
            payload = json.loads(handler.rfile.read(length))
            if not isinstance(payload, dict):
                raise ApiError('Objet JSON requis.')
        if parsed.path == '/expert-api/login' and handler.command == 'POST':
            address = handler.client_address[0]
            with WRITE_LOCK:
                now = time.time()
                attempts = [t for t in _login_attempts.get(address, []) if now - t < 60]
                if len(attempts) >= 20:
                    raise ApiError('Trop de tentatives. Réessayez dans une minute.', 429)
                if len(_login_attempts) > 4096:
                    _login_attempts.clear()
                _login_attempts[address] = [*attempts, now]
            send(store.login(str(payload.get('username', ''))[:64], str(payload.get('password', ''))[:1000]))
            return True
        token = handler.headers.get('Authorization', '').removeprefix('Bearer ')
        user = store.user(token)
        query = parse_qs(parsed.query)
        route = parsed.path.removeprefix('/expert-api/')
        if route == 'me' and handler.command == 'GET':
            send({'user': user})
        elif route == 'logout' and handler.command == 'POST':
            with store.connect() as db:
                db.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(token.encode()).hexdigest(),))
            send({'ok': True})
        elif route == 'tasks' and handler.command == 'GET':
            send(store.listing(user, query))
        elif route == 'tasks' and handler.command == 'POST':
            send({'item': store.save(payload, user)})
        elif route.startswith('review/') and handler.command == 'POST':
            send({'item': store.review(route.split('/')[1], payload, user)})
        elif route == 'documents' and handler.command == 'GET':
            language = query.get('language', [user['languages'][0]])[0]
            store.allowed(user, language)
            with store.connect() as db:
                rows = db.execute('SELECT * FROM documents WHERE language=? ORDER BY name', (language,)).fetchall()
            send({'items': [dict(r) for r in rows]})
        elif route.startswith('audio/') and handler.command == 'GET':
            task = store.task(route.split('/')[1], user)
            if task['kind'] != 'audio':
                raise ApiError('Audio introuvable.', 404)
            path = (store.root / task['data']['audio_file']).resolve()
            if store.root / 'audio' not in path.parents:
                raise ApiError('Chemin audio interdit.', 403)
            size = path.stat().st_size
            start, end, status = 0, size - 1, 200
            range_header = handler.headers.get('Range')
            if range_header:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
                if not match or not any(match.groups()):
                    raise ApiError('Plage audio invalide.', 416)
                left, right = match.groups()
                if left:
                    start = int(left); end = min(size - 1, int(right)) if right else size - 1
                else:
                    start = max(0, size - int(right))
                if start > end or start >= size:
                    raise ApiError('Plage audio invalide.', 416)
                status = 206
            handler.send_response(status)
            handler.send_header('Content-Type', 'audio/wav')
            handler.send_header('Accept-Ranges', 'bytes')
            handler.send_header('Cache-Control', 'private, no-store')
            handler.send_header('Content-Length', str(end - start + 1))
            if status == 206:
                handler.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            handler.end_headers()
            with path.open('rb') as stream:
                stream.seek(start); remaining = end - start + 1
                while remaining:
                    block = stream.read(min(65536, remaining))
                    if not block: break
                    handler.wfile.write(block); remaining -= len(block)
        else:
            raise ApiError('Route inconnue.', 404)
    except ApiError as error:
        send({'error': str(error)}, error.status)
    except (ValueError, TypeError, KeyError):
        send({'error': 'Données invalides.'}, 400)
    except (OSError, sqlite3.Error):
        send({'error': 'Stockage temporairement indisponible.'}, 503)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(os.environ.get('EXPERT_DATA_DIR') or Path(__file__).parent / 'expert_data'))
    sub = parser.add_subparsers(dest='command', required=True)
    account = sub.add_parser('create-user')
    account.add_argument('username'); account.add_argument('--role', choices=['expert', 'reviewer'], default='expert')
    account.add_argument('--languages', nargs='+', choices=LANGUAGES, required=True)
    segments = sub.add_parser('import-audio'); segments.add_argument('tts_root', type=Path)
    document = sub.add_parser('import-word'); document.add_argument('file', type=Path)
    document.add_argument('--language', choices=LANGUAGES, required=True); document.add_argument('--source-file', default='')
    dictionary = sub.add_parser('import-dictionary'); dictionary.add_argument('source', type=Path)
    proposals = sub.add_parser('import-proposals'); proposals.add_argument('file', type=Path)
    export = sub.add_parser('export'); export.add_argument('destination', type=Path)
    args = parser.parse_args(); store = ExpertStore(args.directory)
    if args.command == 'create-user':
        first = getpass.getpass('Mot de passe (12 caractères minimum) : ')
        if first != getpass.getpass('Confirmer : '):
            raise SystemExit('Les mots de passe diffèrent.')
        store.create_user(args.username, first, args.role, args.languages); print('Compte enregistré.')
    elif args.command == 'import-audio':
        print(json.dumps({'imported': store.import_segments(args.tts_root)}))
    elif args.command == 'import-word':
        print(json.dumps({'id': store.import_docx(args.file, args.language, args.source_file)}))
    elif args.command == 'import-dictionary':
        print(json.dumps({'imported': store.import_dictionary(args.source)}))
    elif args.command == 'import-proposals':
        print(json.dumps({'imported': store.import_proposals(args.file)}))
    elif args.command == 'export':
        print(json.dumps(store.export(args.destination), ensure_ascii=True))


if __name__ == '__main__':
    main()
