"""실제 admin 로그인·세션 복원·폐기와 개발 계정의 접근 경계를 확인한다."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from backend.app.api import create_app
from backend.app import local_auth


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 테스트별 앱과 쿠키 저장소를 분리하고 로컬 접속 주소를 사용한다.
    monkeypatch.setenv("LOCAL_ADMIN_LOGIN", "1")
    app = create_app(review_root=tmp_path / "reviews")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as browser:
        yield browser


def csrf_headers(client):
    # 화면 어댑터와 동일하게 CSRF 쿠키를 발급받은 뒤 토큰을 전송한다.
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return {"X-CSRFToken": response.json()["csrf_token"], "Origin": "http://127.0.0.1"}


def test_admin_login_refresh_logout_and_old_cookie_rejection(client):
    # 로그인 뒤 세션을 복원하고 로그아웃한 쿠키의 재사용을 거절한다.
    assert client.get("/api/v1/auth/me").json() == {"authenticated": False, "user": None}
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}, headers=csrf_headers(client))
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    old_cookie = client.cookies.get(local_auth.SESSION_COOKIE)
    session = client.get("/api/v1/auth/me")
    assert session.headers["cache-control"] == "no-store"
    assert session.json()["authenticated"] is True
    assert session.json()["user"]["username"] == "admin"
    assert "password" not in session.text
    assert client.post("/api/v1/auth/logout", headers=csrf_headers(client)).status_code == 204
    client.cookies.set(local_auth.SESSION_COOKIE, old_cookie)
    assert client.get("/api/v1/auth/me").json()["authenticated"] is False


@pytest.mark.parametrize("username,password", [("admin", "wrong"), ("wrong", "admin"), ("관리자", "암호"), ("", "")])
def test_wrong_credentials_do_not_create_session(client, username, password):
    # 공란과 비 ASCII 입력도 서버 오류 없이 로그인 실패로 처리한다.
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password}, headers=csrf_headers(client))
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"
    assert client.get("/api/v1/auth/me").json()["authenticated"] is False


@pytest.mark.parametrize("headers", [{}, {"X-CSRFToken": "wrong"}, {"Origin": "https://external.example"}])
def test_missing_csrf_or_external_origin_cannot_login(client, headers):
    # 외부 출처나 잘못된 토큰은 올바른 계정 입력으로도 로그인할 수 없다.
    valid_headers = csrf_headers(client)
    if "Origin" in headers:
        headers = {**valid_headers, **headers}
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "CSRF_FAILED"
    assert client.get("/api/v1/auth/me").json()["authenticated"] is False


def test_expired_session_and_restart_do_not_restore_login(client, tmp_path, monkeypatch):
    # 세션 만료와 서버 재시작 이후에는 이전 쿠키로 로그인 상태를 복원하지 않는다.
    clock = [100.0]
    monkeypatch.setattr(local_auth, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}, headers=csrf_headers(client))
    old_cookie = client.cookies.get(local_auth.SESSION_COOKIE)
    restarted = create_app(review_root=tmp_path / "restart")
    with TestClient(restarted, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as other:
        other.cookies.set(local_auth.SESSION_COOKIE, old_cookie)
        assert other.get("/api/v1/auth/me").json()["authenticated"] is False
    clock[0] += local_auth.SESSION_SECONDS + 1
    assert client.get("/api/v1/auth/me").json()["authenticated"] is False


@pytest.mark.parametrize("address,host", [("192.0.2.1", "127.0.0.1"), ("127.0.0.1", "external.example")])
def test_nonlocal_requests_cannot_use_development_auth(tmp_path, monkeypatch, address, host):
    # 원격 접속과 외부 호스트 요청에는 임시 인증 경로가 노출되지 않는다.
    monkeypatch.setenv("LOCAL_ADMIN_LOGIN", "1")
    app = create_app(review_root=tmp_path)
    with TestClient(app, base_url=f"http://{host}", client=(address, 12345)) as other:
        for path in ["me", "csrf"]:
            assert other.get(f"/api/v1/auth/{path}").status_code == 404


def test_development_login_can_be_disabled(tmp_path, monkeypatch):
    # 인증 서버를 연결할 때 설정 하나로 개발 계정 경로를 제거할 수 있다.
    monkeypatch.setenv("LOCAL_ADMIN_LOGIN", "0")
    app = create_app(review_root=tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as other:
        assert other.get("/api/v1/auth/me").status_code == 404


@pytest.mark.parametrize("allow_admin", ["0", "1"])
def test_demo_development_login_is_opt_in_and_sessions_keep_their_users(tmp_path, monkeypatch, allow_admin):
    origin = "https://review.example"
    proxy_key = "test-proxy-key-" * 3
    for name, value in {"AUTH_MODE": "demo", "PUBLIC_ORIGIN": origin, "DEMO_USERNAME": "reviewer",
                        "DEMO_PASSWORD": "test-demo-password", "API_PROXY_SECRET": proxy_key,
                        "DEMO_ALLOW_LOCAL_ADMIN": allow_admin}.items():
        monkeypatch.setenv(name, value)
    app = create_app(review_root=tmp_path)
    headers = {"X-Review-Proxy-Key": proxy_key}
    with TestClient(app, base_url=origin, headers=headers) as admin, TestClient(app, base_url=origin, headers=headers) as reviewer:
        admin_headers = {**csrf_headers(admin), "Origin": origin}
        response = admin.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}, headers=admin_headers)
        assert response.status_code == (200 if allow_admin == "1" else 401)
        reviewer_headers = {**csrf_headers(reviewer), "Origin": origin}
        assert reviewer.post("/api/v1/auth/login", json={"username": "reviewer", "password": "test-demo-password"}, headers=reviewer_headers).status_code == 200
        assert reviewer.get("/api/v1/auth/me").json()["user"]["username"] == "reviewer"
        session = admin.get("/api/v1/auth/me").json()
        assert session["authenticated"] is (allow_admin == "1")
        if allow_admin == "1":
            assert session["user"]["username"] == "admin"
            assert admin.post("/api/v1/auth/logout", headers=admin_headers).status_code == 204
            assert admin.get("/api/v1/auth/me").json()["authenticated"] is False
            assert reviewer.get("/api/v1/auth/me").json()["authenticated"] is True
