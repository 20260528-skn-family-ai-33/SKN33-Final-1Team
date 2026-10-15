"""로컬 개발 계정과 외부 시연 계정의 메모리 세션."""
from ipaddress import ip_address
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field


SESSION_COOKIE = "baro_local_session"
CSRF_COOKIE = "csrftoken"
SESSION_SECONDS = 8 * 60 * 60
LOCAL_USER = {"id": "local-admin", "username": "admin", "display_name": "관리자 (개발용)"}
LOCAL_PASSWORD = "admin"


class LocalCredentials(BaseModel):
    """임시 로그인에서 허용하는 자격 증명 필드만 받는다."""
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=128)
    password: str = Field(max_length=128)


def create_auth():
    """앱별 세션을 공유하는 인증 라우터와 API 접근 검사를 만든다."""
    mode = os.getenv("AUTH_MODE", "local").strip().lower()
    if mode not in {"local", "demo"}:
        raise RuntimeError("AUTH_MODE must be local or demo")
    demo = mode == "demo"
    public_origin = os.getenv("PUBLIC_ORIGIN", "").rstrip("/")
    username = os.getenv("DEMO_USERNAME", "reviewer") if demo else "admin"
    password = os.getenv("DEMO_PASSWORD", "") if demo else LOCAL_PASSWORD
    allow_local_admin = demo and os.getenv("DEMO_ALLOW_LOCAL_ADMIN", "0") == "1"
    proxy_secret = os.getenv("API_PROXY_SECRET", "")
    if demo:
        origin = urlsplit(public_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment):
            raise RuntimeError("PUBLIC_ORIGIN must be an HTTPS origin")
        if not username or len(password) < 16 or len(proxy_secret) < 32:
            raise RuntimeError("Demo credentials and API_PROXY_SECRET must be configured")
    user = {"id": "demo-reviewer", "username": username, "display_name": "시연 계정"} if demo else LOCAL_USER
    sessions: dict[str, tuple[float, dict]] = {}

    def require_proxy(request: Request):
        """외부 시연 API는 Pages의 서버 간 중계를 통해서만 받는다."""
        supplied = request.headers.get("X-Review-Proxy-Key", "")
        if demo and not secrets.compare_digest(supplied.encode(), proxy_secret.encode()):
            raise HTTPException(403, {"code": "proxy_forbidden", "message": "허용되지 않은 요청입니다."})

    def require_auth_request(request: Request):
        if demo:
            require_proxy(request)
            return
        try:
            local_client = bool(request.client and ip_address(request.client.host).is_loopback)
        except ValueError:
            local_client = False
        if not local_client or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(404, "인증 경로를 찾을 수 없습니다.")

    router = APIRouter(prefix="/api/v1/auth", dependencies=[Depends(require_auth_request)])

    # 로그인과 로그아웃에서 동일 출처의 CSRF 토큰을 확인한다.
    def verify_csrf(request: Request):
        token = request.cookies.get(CSRF_COOKIE, "")
        header = request.headers.get("X-CSRFToken", "")
        origin = request.headers.get("Origin")
        allowed_origins = {public_origin} if demo else {str(request.base_url).rstrip("/")}
        if not demo:
            allowed_origins.update(value.strip() for value in os.getenv(
                "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",") if value.strip())
        if ((demo and origin != public_origin) or (origin and origin not in allowed_origins)
                or not token or not secrets.compare_digest(token.encode(), header.encode())):
            raise HTTPException(403, {"code": "CSRF_FAILED", "message": "보안 확인에 실패했습니다."})

    # 만료된 세션을 정리하고 쿠키에 해당하는 사용자만 반환한다.
    def session_user(request: Request):
        now = time.monotonic()
        for token, (expires_at, _) in list(sessions.items()):
            if expires_at <= now:
                sessions.pop(token, None)
        session = sessions.get(request.cookies.get(SESSION_COOKIE))
        return session[1] if session else None

    def require_session(request: Request):
        """시연 모드에서는 조회에 세션을, 변경 요청에는 CSRF도 요구한다."""
        if not demo:
            return
        require_proxy(request)
        if session_user(request) is None:
            raise HTTPException(401, {"code": "not_authenticated", "message": "로그인해 주세요."})
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            verify_csrf(request)

    # 사용자 정보 응답과 인증 관련 쿠키가 캐시에 남지 않게 한다.
    def no_cache(response: Response):
        response.headers["Cache-Control"] = "no-store"

    @router.get("/csrf")
    async def csrf(response: Response):
        # 요청마다 새 CSRF 토큰을 쿠키와 응답 본문에 함께 제공한다.
        token = secrets.token_urlsafe(32)
        response.set_cookie(CSRF_COOKIE, token, samesite="strict", path="/", secure=demo)
        no_cache(response)
        return {"csrf_token": token}

    @router.get("/me")
    async def me(request: Request, response: Response):
        # 새로고침 시 서버 메모리 세션에서 임시 계정을 복원한다.
        user = session_user(request)
        no_cache(response)
        return {"authenticated": user is not None, "user": user}

    @router.post("/login")
    async def login(credentials: LocalCredentials, request: Request, response: Response):
        # 로컬 계정과 시연 계정을 분리하고 로그인마다 새 세션을 발급한다.
        verify_csrf(request)
        login_user = None
        if (secrets.compare_digest(credentials.username.encode(), username.encode())
                and secrets.compare_digest(credentials.password.encode(), password.encode())):
            login_user = user
        elif (allow_local_admin
                and secrets.compare_digest(credentials.username.encode(), LOCAL_USER["username"].encode())
                and secrets.compare_digest(credentials.password.encode(), LOCAL_PASSWORD.encode())):
            login_user = LOCAL_USER
        if login_user is None:
            raise HTTPException(401, {"code": "invalid_credentials", "message": "아이디 또는 비밀번호를 확인해 주세요."})
        session_user(request)
        sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        token = secrets.token_urlsafe(32)
        sessions[token] = (time.monotonic() + SESSION_SECONDS, login_user)
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            samesite="strict", path="/", secure=demo)
        no_cache(response)
        return {"user": login_user}

    @router.post("/logout", status_code=204)
    async def logout(request: Request, response: Response):
        # 서버의 세션을 폐기해 이전 쿠키로 다시 로그인 상태를 복원할 수 없게 한다.
        verify_csrf(request)
        sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=demo)
        response.delete_cookie(CSRF_COOKIE, path="/", samesite="strict", secure=demo)
        no_cache(response)

    # shortcut: 메모리 세션은 재시작 시 만료된다. 다중 서버는 공용 세션 저장소로 교체한다.
    return router, require_session, require_proxy
