"""Video routes keep the PostgreSQL facade's session boundary after integration."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.api import create_app
from backend.app.security.errors import AuthError


def test_postgres_video_session_guard_and_restart_recovery(tmp_path, monkeypatch):
    from backend.app.management import maintenance
    from backend.app.routes.v1 import business
    monkeypatch.setattr(maintenance, 'active', lambda engine: False)

    def subject(request):
        if request.cookies.get('test-session') != 'authenticated':
            raise AuthError('AUTHENTICATION_REQUIRED', 401)
        return object(), False

    monkeypatch.setattr(business, 'subject', subject)
    store = SimpleNamespace(settings=SimpleNamespace(hosts={'localhost'}), engine=object(),
                            open=lambda app: None, close=lambda: None)
    root = tmp_path / 'videos'
    interrupted = root / '00000000-0000-0000-0000-000000000001'
    interrupted.mkdir(parents=True)
    (interrupted / 'result.json').write_text(json.dumps({'status': 'running'}))
    app = create_app(postgres_store=store, review_root=tmp_path / 'reviews', video_root=root,
                     video_readiness=lambda: (False, 'test-only unavailable'),
                     video_logo_readiness=lambda: (False, 'test-only unavailable'))
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 12)) as client:
        for path in ('/api/video-reviews/status', '/api/video-reviews',
                     '/api/video-reviews/00000000-0000-0000-0000-000000000001'):
            assert client.get(path).status_code == 401
        client.cookies.set('test-session', 'authenticated')
        assert client.get('/api/video-reviews').status_code == 200
    assert json.loads((interrupted / 'result.json').read_text())['status'] == 'failed'
