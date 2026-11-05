"""Local video API retains the existing auth and CSRF boundary."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app.config import ConfigurationError
from backend.app.v1_config import load_v1_settings
from backend.app.video.framework_service import create_video_app
from tests.app.auth_fixtures import auth_environment


def test_video_service_requires_session_and_csrf_and_stays_local(tmp_path, monkeypatch):
    from backend.app.management import maintenance
    from backend.app.main import create_app as create_auth_app
    from backend.app.persistence import schema_inventory
    from backend.app.services.auth import AuthService

    monkeypatch.setattr(schema_inventory, 'check_schema', lambda *_: None)
    monkeypatch.setattr(maintenance, 'active', lambda *_: False)
    monkeypatch.setattr(AuthService, 'current', lambda self, token, **kwargs:
                        SimpleNamespace(account_kind='user') if token == 'a' * 43 else None)
    settings = load_v1_settings(auth_environment())
    app = create_video_app(settings, tmp_path / 'isolated-video', engine=object(),
                           readiness=lambda: (False, 'synthetic worker unavailable'))
    auth_app = create_auth_app(settings=settings, engine=object())
    # The browser fetches CSRF from the framework API, not the video service.
    with TestClient(auth_app, base_url='http://localhost:8000', client=('127.0.0.1', 1234)) as auth_client:
        auth_client.cookies.set(settings.session_cookie, 'a' * 43)
        csrf = auth_client.get('/api/v1/auth/csrf').json()['csrf_token']
        csrf_cookie = auth_client.cookies.get(settings.csrf_cookie)
    with TestClient(app, base_url='http://localhost:8000', client=('127.0.0.1', 1234)) as client:
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
            'Origin': 'http://localhost:8000', 'X-CSRFToken': csrf})
        assert response.status_code == 422  # Auth/CSRF passed; upload is required.

    with TestClient(app, base_url='http://localhost:8000', client=('203.0.113.1', 1234)) as client:
        client.cookies.set(settings.session_cookie, 'a' * 43)
        assert client.get('/api/video-reviews/status').status_code == 404


def test_local_video_service_rejects_production(tmp_path):
    settings = replace(load_v1_settings(auth_environment()), environment='production')
    with pytest.raises(ConfigurationError):
        create_video_app(settings, tmp_path / 'video', engine=object())
