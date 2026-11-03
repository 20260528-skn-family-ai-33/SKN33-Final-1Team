"""Shared HTTP dependencies, upload validation and response boundaries."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.creative_checker import services as S, auth
from backend.creative_checker.models import User, new_id


EDITORS = {"approver", "editor"}
APPROVERS = {"approver"}


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > 102 * 1024 * 1024:
                    raise HTTPException(422, {"code": "upload_size_limit", "message": "Request exceeds the upload limit"})
            return message

        return await self.app(scope, limited_receive, send)


def error_response(request: Request, status: int, code: str, message: str, headers=None):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message},
                        "request_id": getattr(request.state, "request_id", new_id())}, headers=headers)


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        try:
            yield db
        finally:
            db.rollback()
            S.cleanup_files(db)


Db = Annotated[Session, Depends(get_db)]


def get_identity(request: Request, db: Db):
    return auth.authenticate(request, db, request.app.state.settings)


Identity = Annotated[tuple, Depends(get_identity)]


def admin_user(identity: Identity):
    user = identity[0]
    if not user.is_admin:
        S.fail(403, "admin_required", "An administrator account is required")
    return user


Admin = Annotated[User, Depends(admin_user)]


async def upload_bytes(file: UploadFile, maximum: int) -> tuple[str, bytes]:
    try:
        filename = S.clean_filename(file.filename)
        data = await file.read(maximum + 1)
        if not data or len(data) > maximum:
            S.fail(422, "upload_size_limit", "The uploaded file is empty or exceeds the allowed size")
        return filename, data
    finally:
        await file.close()


def inspect_media(data: bytes, filename: str, kind: str) -> dict:
    from .media import MediaError, inspect_asset

    try:
        return inspect_asset(data, filename, kind)
    except MediaError as exc:
        # Missing local binaries are service readiness problems, not invalid input.
        S.fail(503 if exc.code in {"media_binary_missing", "media_binary_error"} else 422, exc.code, str(exc))
