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


@pytest.mark.parametrize('cookie_name', ['session_cookie', 'legacy_session_cookie'])
def test_aws_video_results_are_private_to_their_owner(tmp_path, monkeypatch, cookie_name):
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
        cookie = getattr(settings, cookie_name)
        for route in ('/api/video-reviews', '/api/video-logos'):
            client.cookies.set(cookie, 'a' * 43)
            assert [item['id'] for item in client.get(route).json()['items']] == [job_id]
            assert client.get(f'{route}/{job_id}').status_code == 200
            client.cookies.set(cookie, 'b' * 43)
            assert client.get(route).json()['items'] == []
            assert client.get(f'{route}/{job_id}').status_code == 404


def test_aws_video_runs_parallel_qwen_subtitles_and_pins_logo_work_to_distinct_workers(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import httpx

    from backend.app.management import maintenance
    from backend.app.persistence import schema_inventory
    from backend.app.services.auth import AuthService
    from backend.app.video import subtitle_vlm as vlm
    from backend.app.video.logos import client as remote
    from tests.app.test_video_review_api import FFMPEG, SUBTITLE, make_video, png_bytes

    if not FFMPEG:
        pytest.skip("FFmpeg is required to prepare and decode the test videos")
    qwen_url = "http://qwen-video.invalid/v1"
    model = vlm.DEFAULT_MODEL
    monkeypatch.setenv("VIDEO_SUBTITLE_ENGINE", "qwen3-vl")
    monkeypatch.setenv("VIDEO_SUBTITLE_VLM_URL", qwen_url)
    monkeypatch.setenv("VIDEO_SUBTITLE_VLM_MODEL", model)
    monkeypatch.delenv("VIDEO_SUBTITLE_VLM_API_KEY", raising=False)
    monkeypatch.delenv("VIDEO_SUBTITLE_VLM_TIMEOUT", raising=False)
    monkeypatch.setenv("VIDEO_SUBTITLE_VLM_CONCURRENCY", "1")
    monkeypatch.setenv("VIDEO_MAX_ACTIVE", "2")
    monkeypatch.setattr(schema_inventory, "check_schema", lambda *_: None)
    monkeypatch.setattr(maintenance, "active", lambda *_: False)
    monkeypatch.setattr("backend.app.persistence.transaction.unit_of_work", lambda _: _no_developer_accounts())
    sessions = {"a" * 43: uuid4(), "b" * 43: uuid4()}
    monkeypatch.setattr(AuthService, "current", lambda self, token, **kwargs:
                        SimpleNamespace(user_id=sessions[token]) if token in sessions else None)
    monkeypatch.setattr(remote, "POLL_SECONDS", 0)

    qwen_barrier = Barrier(2, timeout=12)
    content = json.dumps({"texts": [{"text": SUBTITLE, "role": "subtitle", "region": "bottom"}]})

    def qwen_client(**kwargs):
        first_frame = True

        def respond(request):
            nonlocal first_frame
            if request.method == "GET":
                assert str(request.url) == f"{qwen_url}/models"
                return httpx.Response(200, json={"data": [{"id": model}]})
            assert request.method == "POST" and str(request.url) == f"{qwen_url}/chat/completions"
            payload = json.loads(request.content)
            assert payload["model"] == model
            assert payload["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            # Each review has its own client and one frame in flight, so only two reviews can pass this barrier.
            if first_frame:
                first_frame = False
                qwen_barrier.wait()
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        return httpx.Client(transport=httpx.MockTransport(respond), **kwargs)

    def qwen_get(url, **kwargs):
        with qwen_client() as client:
            return client.get(url, **kwargs)

    monkeypatch.setattr(vlm, "httpx", SimpleNamespace(
        Client=qwen_client, get=qwen_get, Limits=httpx.Limits, HTTPError=httpx.HTTPError,
    ))

    pool = remote.WorkerPool("http://127.0.0.1:18791", "http://127.0.0.1:18792")
    barriers = {kind: Barrier(2, timeout=12) for kind in ("candidates", "compare")}
    posted = {}

    def request(method, path, payload=None, timeout=30, *, endpoint=None):
        url = endpoint or remote.worker_url()
        if path == "/health":
            return {"ready": url in pool.endpoints.values()}
        if method == "POST":
            job_id = uuid4().hex
            posted[job_id] = (url, payload)
            barriers[payload["kind"]].wait()
            return {"id": job_id}
        origin, body = posted[path.rsplit("/", 1)[1]]
        assert url == origin
        if body["kind"] == "candidates":
            result = {"groups": [], "refs": [{"name": "sample"}], "size": [640, 360]}
        else:
            result = [{"outcome": "match", "findings": [], "kinds": [], "color": {}, "elements": [], "ocr": []}
                      for _ in body["crops"]]
        return {"status": "completed", "result": result}

    monkeypatch.setattr(remote, "_request", request)
    base = "https://video-test.example"
    settings = load_v1_settings(auth_environment(APP_ENV="production", LOCAL_ADMIN_LOGIN="0",
                                AUTH_ALLOWED_ORIGINS=base, AUTH_ALLOWED_HOSTS="video-test.example"))
    app = create_video_app(settings, tmp_path / "storage", engine=object(), aws_test=True, worker_pool=pool)
    videos = [make_video(tmp_path / "one.mp4", seconds=2), make_video(tmp_path / "two.mp4", seconds=3)]

    def exercise(client, token, clip):
        client.cookies.set(settings.session_cookie, token)
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        headers = {"Origin": base, "X-CSRFToken": csrf}
        video = ("clip.mp4", clip, "video/mp4")
        response = client.post("/api/video-reviews", headers=headers, files={"video": video})
        assert response.status_code == 202, response.text
        review_id = response.json()["id"]
        review = client.get(f"/api/video-reviews/{review_id}").json()
        assert review["status"] == "completed", review
        assert review["runtime"]["engine"] == "qwen3-vl"
        assert review["runtime"]["model"] == model
        assert any(track["text"] == SUBTITLE and track["recognition_engine"] == vlm.ENGINE
                   for track in review["tracks"])

        response = client.post("/api/video-logos", headers=headers, data={"review_id": review_id},
                               files={"video": video, "logos": ("sample.png", png_bytes(), "image/png")})
        assert response.status_code == 202, response.text
        logo_url = f"/api/video-logos/{response.json()['id']}"
        logo = client.get(logo_url).json()
        assert logo["status"] == "candidates_ready", logo
        assert logo["worker_slot"] == review["worker_slot"]
        response = client.post(f"{logo_url}/compare", headers=headers, json=[
            {"logo": 0, "decision": "done", "crops": [{"source": "manual", "sec": 0, "bbox": [10, 10, 110, 100]}]},
        ])
        assert response.status_code == 202, response.text
        compare = client.get(f"{logo_url}/compare").json()
        assert compare["compare_status"] == "completed", compare
        assert compare["bundles"][0]["evidence"]["outcomes"] == {"match": 1}
        return review["worker_slot"]

    with (TestClient(app, base_url=base, client=("127.0.0.1", 1234)) as first,
          TestClient(app, base_url=base, client=("127.0.0.1", 1235)) as second,
          ThreadPoolExecutor(max_workers=2) as threads):
        results = [threads.submit(exercise, client, token, clip)
                   for client, token, clip in zip((first, second), sessions, videos)]
        assert {result.result(timeout=60) for result in results} == set(pool.endpoints)
    for kind in barriers:
        assert {url for url, body in posted.values() if body["kind"] == kind} == set(pool.endpoints.values())


@pytest.mark.parametrize("secondary", ["http://127.0.0.1:18791", "http://10.0.0.2:18792", "http://127.0.0.1:65536"])
def test_aws_video_worker_pool_requires_distinct_loopback_ports(secondary):
    from backend.app.video.logos.client import WorkerPool

    with pytest.raises(ValueError, match="distinct loopback"):
        WorkerPool("http://127.0.0.1:18791", secondary)


def _no_developer_accounts():
    from contextlib import nullcontext
    result = SimpleNamespace(scalar_one=lambda: False)
    return nullcontext(SimpleNamespace(exec_driver_sql=lambda _query: result))
