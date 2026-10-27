"""운영체제 경로가 달라도 React 직접 접근과 API·정적 파일 응답을 유지한다."""

import ntpath
import pytest
from fastapi.testclient import TestClient

from backend.app import api as backend
from backend.app.spa import ReviewStaticFiles


@pytest.fixture(params=["native", "windows"])
def spa_client(tmp_path, monkeypatch, request):
    """같은 요청을 기본 경로와 Windows 내부 경로로 각각 검사한다."""
    if request.param == "windows":
        get_path = ReviewStaticFiles.get_path
        lookup_path = ReviewStaticFiles.lookup_path

        def windows_get_path(self, scope):
            """현재 운영체제와 무관하게 Windows의 경로 구분자를 재현한다."""
            return ntpath.normpath(get_path(self, scope))

        def host_lookup_path(self, path):
            """모의 Windows 경로도 테스트 파일은 실제 운영체제에서 찾게 한다."""
            return lookup_path(self, path.replace("\\", "/"))

        monkeypatch.setattr(ReviewStaticFiles, "get_path", windows_get_path)
        monkeypatch.setattr(ReviewStaticFiles, "lookup_path", host_lookup_path)

    # 실제 빌드 유무와 무관하게 공개 앱의 라우팅 경계를 검증한다.
    frontend = tmp_path / "frontend" / "dist"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text('<!doctype html><div id="root">SPA fixture</div>', encoding="utf-8")
    (assets / "app.js").write_text("window.fixture = true;", encoding="utf-8")
    monkeypatch.setattr(backend, "PROJECT_ROOT", tmp_path)
    application = backend.create_app(review_root=tmp_path / "reviews")
    with TestClient(application) as client:
        yield client


@pytest.mark.parametrize("path", ["/", "/login", "/login/", "/review/new", "/review/new/", "/video", "/video/"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_browser_navigation_and_refresh_return_react_html(spa_client, path, method):
    # 브라우저의 문서 탐색과 HEAD 확인 요청에만 React 진입점을 제공한다.
    response = spa_client.request(method, path, headers={"Accept": "text/html,application/xhtml+xml"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    if method == "GET":
        assert "SPA fixture" in response.text
    else:
        assert response.content == b""


@pytest.mark.parametrize("path", [
    "/api/v1/auth/me", "/api/v1/auth/csrf", "/api/does-not-exist",
    "/assets/missing.js", "/missing.css", "/unknown", "/review/new/missing",
])
def test_missing_apis_assets_and_unknown_pages_stay_404(spa_client, path):
    # API·파일 오류가 성공 HTML로 가려져 로그인 성공으로 오인되지 않아야 한다.
    response = spa_client.get(path, headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert "SPA fixture" not in response.text


@pytest.mark.parametrize("path", ["/login", "/review/new"])
@pytest.mark.parametrize("accept", ["application/json", "*/*"])
def test_non_document_requests_do_not_receive_fallback(spa_client, path, accept):
    # fetch 요청에는 문서용 fallback을 적용하지 않는다.
    assert spa_client.get(path, headers={"Accept": accept}).status_code == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_mutating_requests_do_not_receive_fallback(spa_client, method):
    # 문서 URL을 대상으로 한 잘못된 쓰기 요청을 정상 페이지로 바꾸지 않는다.
    response = spa_client.request(method, "/login", headers={"Accept": "text/html"})
    assert response.status_code == 405
    assert "SPA fixture" not in response.text


def test_existing_static_files_keep_original_contents(spa_client):
    # 정상 정적 파일은 원래의 내용과 MIME 타입을 그대로 제공한다.
    response = spa_client.get("/assets/app.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.text == "window.fixture = true;"
