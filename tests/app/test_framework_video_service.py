"""Local video API retains the existing auth and CSRF boundary."""
from dataclasses import replace
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import ConfigurationError
from backend.app.v1_config import load_v1_settings
from backend.app.video.framework_service import create_video_app
from tests.app.auth_fixtures import auth_environment


@pytest.mark.parametrize('base',['http://localhost:8000','http://192.168.0.144:5173'])
def test_video_service_requires_session_and_csrf_and_stays_local(tmp_path, monkeypatch,base):
    from backend.app.management import maintenance
    from backend.app.main import create_app as create_auth_app
    from backend.app.persistence import schema_inventory
    from backend.app.services.auth import AuthService

    monkeypatch.setattr(schema_inventory, 'check_schema', lambda *_: None)
    monkeypatch.setattr(maintenance, 'active', lambda *_: False)
    monkeypatch.setattr(AuthService, 'current', lambda self, token, **kwargs:
                        SimpleNamespace(account_kind='user') if token == 'a' * 43 else None)
    from urllib.parse import urlsplit
    settings = load_v1_settings(auth_environment(APP_ENV='development',LOCAL_ADMIN_LOGIN='0',
        AUTH_ALLOWED_ORIGINS=base,AUTH_ALLOWED_HOSTS=urlsplit(base).hostname))
    app = create_video_app(settings, tmp_path / 'isolated-video', engine=object(),
                           readiness=lambda: (False, 'synthetic worker unavailable'))
    auth_app = create_auth_app(settings=settings, engine=object())
    # The browser fetches CSRF from the framework API, not the video service.
    with TestClient(auth_app, base_url=base, client=('127.0.0.1', 1234)) as auth_client:
        auth_client.cookies.set(settings.session_cookie, 'a' * 43)
        csrf = auth_client.get('/api/v1/auth/csrf').json()['csrf_token']
        csrf_cookie = auth_client.cookies.get(settings.csrf_cookie)
    with TestClient(app, base_url=base, client=('127.0.0.1', 1234)) as client:
        assert client.get('/api/video-reviews/status').status_code == 401
        assert client.get('/api/video-logos/status').status_code == 401
        client.cookies.set(settings.session_cookie, 'a' * 43)
        video = client.get('/api/video-reviews/status')
        assert video.status_code == 200
        assert video.json()['ready'] is False
        assert client.get('/api/video-logos/status').status_code == 200
        # A write without a same-origin, session-bound token is rejected before upload.
        assert client.post('/api/video-reviews').status_code == 403
        client.cookies.set(settings.csrf_cookie, csrf_cookie)
        response = client.post('/api/video-reviews', headers={
            'Origin': base, 'X-CSRFToken': csrf})
        assert response.status_code == 422  # Auth/CSRF passed; upload is required.

    with TestClient(app, base_url=base, client=('203.0.113.1', 1234)) as client:
        client.cookies.set(settings.session_cookie, 'a' * 43)
        assert client.get('/api/video-reviews/status').status_code == 404


def test_local_video_service_rejects_production(tmp_path):
    settings = replace(load_v1_settings(auth_environment()), environment='production')
    with pytest.raises(ConfigurationError):
        create_video_app(settings, tmp_path / 'video', engine=object())


def test_aws_video_results_are_private_to_their_owner(tmp_path, monkeypatch):
    from backend.app.management import maintenance
    from backend.app.persistence import schema_inventory
    from backend.app.services.auth import AuthService

    owner, other = uuid4(), uuid4()
    sessions = {'a' * 43: owner, 'b' * 43: other}
    monkeypatch.setattr(schema_inventory, 'check_schema', lambda *_: None)
    monkeypatch.setattr(maintenance, 'active', lambda *_: False)
    monkeypatch.setattr('backend.app.persistence.transaction.unit_of_work',
                        lambda _engine: _no_developer_accounts())
    monkeypatch.setattr(AuthService, 'current', lambda self, token, **kwargs:
                        SimpleNamespace(user_id=sessions[token]) if token in sessions else None)
    base = 'https://video-test.example'
    settings = load_v1_settings(auth_environment(APP_ENV='production', LOCAL_ADMIN_LOGIN='0',
                                AUTH_ALLOWED_ORIGINS=base, AUTH_ALLOWED_HOSTS='video-test.example'))
    job_id = str(uuid4())
    for section in ('reviews', 'logos'):
        target = tmp_path / section / str(owner) / job_id
        target.mkdir(parents=True)
        (target / 'result.json').write_text(json.dumps({'id': job_id, 'status': 'completed'}))
    app = create_video_app(settings, tmp_path, engine=object(), aws_test=True)
    with TestClient(app, base_url=base, client=('127.0.0.1', 1234)) as client:
        for route in ('/api/video-reviews', '/api/video-logos'):
            client.cookies.set(settings.session_cookie, 'a' * 43)
            assert [item['id'] for item in client.get(route).json()['items']] == [job_id]
            assert client.get(f'{route}/{job_id}').status_code == 200
            client.cookies.set(settings.session_cookie, 'b' * 43)
            assert client.get(route).json()['items'] == []
            assert client.get(f'{route}/{job_id}').status_code == 404


def _no_developer_accounts():
    from contextlib import nullcontext
    result = SimpleNamespace(scalar_one=lambda: False)
    return nullcontext(SimpleNamespace(exec_driver_sql=lambda _query: result))
