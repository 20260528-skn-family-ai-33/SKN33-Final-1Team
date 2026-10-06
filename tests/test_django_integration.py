import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store
from django.core.management import CommandError
from inspections.models import InspectionRun


@pytest.fixture
def client(tmp_path):
    application = create_app(tmp_path / 'runtime')
    application.state.store.prepare()
    with TestClient(application, base_url='http://localhost') as instance:
        yield instance


def test_django_template_static_admin_and_fastapi_routes(client):
    page = client.get('/')
    assert page.status_code == 200
    assert '동작그만' in page.text
    assert 'name="csrf-token"' in page.text
    assert '/static/app.js' in page.text
    assert '/static/app.js?v=' in page.text and '/static/styles.css?v=' in page.text
    assert '{%' not in page.text and '{{ csrf_token }}' not in page.text
    assert client.cookies.get('csrftoken')
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/web/styles.css').status_code == 200
    assert client.get('/static/admin/css/base.css').status_code == 200
    assert client.get('/web/index.html').status_code == 404
    assert client.get('/static/../manage.py').status_code == 404
    assert client.get('/admin/', follow_redirects=False).status_code == 302
    assert client.get('/admin/login/').status_code == 200
    assert client.get('/docs').status_code == 200
    assert '/api/runs' in client.get('/openapi.json').json()['paths']
    health = client.get('/api/health').json()
    assert health['web_framework'] == 'Django'
    assert health['api_framework'] == 'FastAPI'


def test_csrf_requires_cookie_matching_header_and_same_origin(client):
    version = client.get('/api/criteria').json()['version']
    payload = {'version': version}
    assert client.post('/api/criteria/confirm', json=payload).status_code == 403
    token = client.get('/api/csrf').json()['csrf_token']
    assert client.post('/api/criteria/confirm', json=payload).status_code == 403
    assert client.post('/api/criteria/confirm', json=payload, headers={'X-CSRFToken': 'invalid'}).status_code == 403
    headers = {'X-CSRFToken': token, 'Origin': 'https://unrelated.example'}
    assert client.post('/api/criteria/confirm', json=payload, headers=headers).status_code == 403
    assert client.get('/api/criteria').json()['confirmed'] is False
    headers['Origin'] = 'http://localhost'
    assert client.post('/api/criteria/confirm', json=payload, headers=headers).status_code == 200
    client.cookies.clear()
    assert client.post('/api/criteria/confirm', json=payload, headers=headers).status_code == 403
    assert client.get('/api/health', headers={'Host': 'unrelated.example'}).status_code == 400


def make_legacy_database(path):
    asset = {'id': 'legacy-asset', 'name': '딸기라떼.png', 'storage_name': 'legacy-asset.png'}
    run = {'id': 'legacy-run', 'created_at': '2026-10-02T10:00:00+00:00',
           'status': 'partial', 'asset_id': asset['id'],
           'criteria_snapshot': {'version': 'legacy-version', 'rules': [{'expected': '딸기라떼'}]},
           'results': [{'verdict': '확인 필요', 'evidence': [{'text': '딸기라떼', 'bbox': [0.1, 0.2, 0.3, 0.4]}]}]}
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE confirmations (version TEXT PRIMARY KEY, confirmed_at TEXT NOT NULL);
            CREATE TABLE assets (id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE runs (id TEXT PRIMARY KEY, idem_key TEXT UNIQUE NOT NULL,
                fingerprint TEXT NOT NULL, created_at TEXT NOT NULL, document TEXT NOT NULL);
        ''')
        db.execute('INSERT INTO confirmations VALUES (?,?)', ('legacy-version', run['created_at']))
        db.execute('INSERT INTO assets VALUES (?,?)', (asset['id'], json.dumps(asset, ensure_ascii=False)))
        db.execute('INSERT INTO runs VALUES (?,?,?,?,?)',
                   (run['id'], 'legacy-key', 'legacy-fingerprint', run['created_at'], json.dumps(run, ensure_ascii=False)))
    return asset, run


def test_legacy_adoption_backup_snapshots_ordering_and_repeatability(tmp_path):
    path = tmp_path / 'latte.sqlite3'
    asset, legacy = make_legacy_database(path)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    original_image = uploads / asset['storage_name']
    original_image.write_bytes(b'existing image bytes')
    store = Store(path)
    backup = store.prepare()
    assert backup.is_file()
    with sqlite3.connect(backup) as old:
        assert json.loads(old.execute('SELECT document FROM runs').fetchone()[0]) == legacy
        assert old.execute('SELECT created_at FROM runs').fetchone()[0] == legacy['created_at']
        assert not old.execute("SELECT name FROM sqlite_master WHERE name='django_migrations'").fetchall()
    assert store.confirmed('legacy-version')
    assert store.asset(asset['id']) == asset
    assert store.run(legacy['id']) == legacy
    assert original_image.read_bytes() == b'existing image bytes'
    repeated = {**legacy, 'id': 'ignored'}
    assert store.reserve_run(repeated, 'legacy-key', 'legacy-fingerprint') == (legacy, True)
    assert store.reserve_run(repeated, 'legacy-key', 'different') == (legacy, False)
    newer = {**legacy, 'id': 'new-run', 'created_at': '2026-10-02T11:00:00+00:00'}
    assert store.reserve_run(newer, 'new-key', 'new-fingerprint') == (None, True)
    assert [row['id'] for row in store.runs()] == ['new-run', 'legacy-run']
    assert store.prepare() is None
    assert len(list((tmp_path / 'backups').glob('*.sqlite3'))) == 1
    assert len(store.runs()) == 2


def test_incomplete_legacy_schema_stops_without_changing_original(tmp_path):
    path = tmp_path / 'latte.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE assets (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        db.execute('INSERT INTO assets VALUES (?,?)', ('keep-me', '{}'))
    original = path.read_bytes()
    with pytest.raises(CommandError, match='일부 누락'):
        Store(path).prepare()
    assert path.read_bytes() == original


def test_concurrent_duplicate_reservations_and_database_isolation(tmp_path):
    store = Store(tmp_path / 'first' / 'latte.sqlite3')
    store.prepare()

    def reserve(index):
        return store.reserve_run({'id': str(index), 'created_at': '2026-10-06T01:00:00+00:00'}, 'same-key', 'same-input')

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(previous is None for previous, _ in results) == 1
    assert all(matches for _, matches in results)
    assert len(store.runs()) == 1
    other = Store(tmp_path / 'second' / 'latte.sqlite3')
    other.prepare()
    assert other.runs() == []
    with store.session():
        assert InspectionRun.objects.using(store.alias).count() == 1
