"""로컬 화면 개발을 위한 임시 admin 계정과 메모리 세션."""
from ipaddress import ip_address
import os
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field


SESSION_COOKIE = "baro_local_session"
CSRF_COOKIE = "csrftoken"
SESSION_SECONDS = 8 * 60 * 60
LOCAL_USER = {"id": "local-admin", "username": "admin", "display_name": "관리자 (개발용)"}


class LocalCredentials(BaseModel):
    """임시 로그인에서 허용하는 자격 증명 필드만 받는다."""
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=128)
    password: str = Field(max_length=128)


def local_auth_router() -> APIRouter:
    """앱마다 독립된 세션을 만들고 localhost에서만 임시 계정을 제공한다."""
    sessions: dict[str, float] = {}

    # 원격 접속에서는 고정 개발 계정의 인증 경로를 제공하지 않는다.
    def require_local_request(request: Request):
        try:
            local_client = bool(request.client and ip_address(request.client.host).is_loopback)
        except ValueError:
            local_client = False
        if not local_client or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(404, "인증 경로를 찾을 수 없습니다.")

    router = APIRouter(prefix="/api/v1/auth", dependencies=[Depends(require_local_request)])

    # 로그인과 로그아웃에서 동일 출처의 CSRF 토큰을 확인한다.
    def verify_csrf(request: Request):
        token = request.cookies.get(CSRF_COOKIE, "")
        header = request.headers.get("X-CSRFToken", "")
        origin = request.headers.get("Origin")
        allowed_origins = {str(request.base_url).rstrip("/")}
        allowed_origins.update(value.strip() for value in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",") if value.strip())
        if (origin and origin not in allowed_origins) or not token or not secrets.compare_digest(token.encode(), header.encode()):
            raise HTTPException(403, {"code": "CSRF_FAILED", "message": "보안 확인에 실패했습니다."})

    # 만료된 세션을 정리하고 쿠키에 해당하는 사용자만 반환한다.
    def session_user(request: Request):
        now = time.monotonic()
        for token, expires_at in list(sessions.items()):
            if expires_at <= now:
                sessions.pop(token, None)
        return LOCAL_USER if request.cookies.get(SESSION_COOKIE) in sessions else None

    # 사용자 정보 응답과 인증 관련 쿠키가 캐시에 남지 않게 한다.
    def no_cache(response: Response):
        response.headers["Cache-Control"] = "no-store"

    @router.get("/csrf")
    async def csrf(response: Response):
        # 요청마다 새 CSRF 토큰을 쿠키와 응답 본문에 함께 제공한다.
        token = secrets.token_urlsafe(32)
        response.set_cookie(CSRF_COOKIE, token, samesite="strict", path="/", secure=False)
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
        # admin/admin만 승인하고 로그인마다 예측 불가능한 새 세션을 발급한다.
        verify_csrf(request)
        if not (secrets.compare_digest(credentials.username.encode(), b"admin")
                and secrets.compare_digest(credentials.password.encode(), b"admin")):
            raise HTTPException(401, {"code": "invalid_credentials", "message": "아이디 또는 비밀번호를 확인해 주세요."})
        session_user(request)
        sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        token = secrets.token_urlsafe(32)
        sessions[token] = time.monotonic() + SESSION_SECONDS
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            samesite="strict", path="/", secure=False)
        no_cache(response)
        return {"user": LOCAL_USER}

    @router.post("/logout", status_code=204)
    async def logout(request: Request, response: Response):
        # 서버의 세션을 폐기해 이전 쿠키로 다시 로그인 상태를 복원할 수 없게 한다.
        verify_csrf(request)
        sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
        response.delete_cookie(CSRF_COOKIE, path="/", samesite="strict")
        no_cache(response)

    return router
