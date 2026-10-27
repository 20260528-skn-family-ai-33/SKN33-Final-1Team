"""React 페이지의 직접 접근을 지원하는 정적 파일 응답."""
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class ReviewStaticFiles(StaticFiles):
    """등록된 화면 경로만 index.html로 연결하고 API·파일 오류는 보존한다."""

    async def get_response(self, path, scope):
        # 실제 파일을 먼저 찾고, 없는 페이지에 대해서만 React 진입점을 반환한다.
        try:
            response = await super().get_response(path, scope)
            if response.status_code != 404:
                return response
        except HTTPException as error:
            if error.status_code != 404:
                raise
            response = None

        headers = dict(scope.get("headers", []))
        accepts_html = b"text/html" in headers.get(b"accept", b"").lower()
        # Windows 내부 경로도 화면 URL과 비교할 때는 슬래시로 통일한다.
        page_path = "/" + path.replace("\\", "/").strip("/")
        if scope["method"] in {"GET", "HEAD"} and accepts_html and page_path in {"/login", "/review/new", "/video"}:
            return await super().get_response("index.html", scope)
        if response is not None:
            return response
        raise HTTPException(status_code=404)
