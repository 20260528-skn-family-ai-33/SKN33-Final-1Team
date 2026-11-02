"""The rebuilt FastAPI entry point. No model is loaded by the HTTP process."""

from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import logging
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, contracts as C, services as S
from .database import create_engine, session_factory
from .models import (
    Approval, Asset, AssetVersion, AuthSession, ChangeRequest, CriterionSet, Document,
    Event, FileRecord, Job, Logo, Membership, Outbox, Project, Run, User, new_id, utcnow,
)
from .settings import Settings, get_settings
from .storage import create_storage


EDITORS = {"approver", "editor"}
APPROVERS = {"approver"}
logger = logging.getLogger(__name__)


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


def create_app(settings: Settings | None = None, *, engine=None, storage=None) -> FastAPI:
    settings = settings or get_settings()
    owns_engine = engine is None
    engine = engine if engine is not None else create_engine(settings.database_url)
    storage = storage if storage is not None else create_storage(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owns_engine:
            engine.dispose()

    app = FastAPI(title="Creative Checker", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory(engine)
    app.state.storage = storage

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = new_id()
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                auth.ensure_same_origin(request)
                length = request.headers.get("content-length")
                if length is not None:
                    try:
                        parsed = int(length)
                    except ValueError:
                        return error_response(request, 422, "invalid_content_length", "Invalid Content-Length")
                    if parsed < 0 or parsed > 102 * 1024 * 1024:
                        return error_response(request, 422, "upload_size_limit", "Request exceeds the upload limit")
            response = await call_next(request)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": "request_rejected", "message": str(exc.detail)}
            response = error_response(request, exc.status_code, detail["code"], detail["message"], exc.headers)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        codes = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid_input", 503: "unavailable"}
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": codes.get(exc.status_code, "request_rejected"), "message": str(exc.detail)}
        return error_response(request, exc.status_code, detail["code"], detail["message"], exc.headers)

    @app.exception_handler(RequestValidationError)
    async def input_error(request: Request, exc: RequestValidationError):
        # ValidationError.input can contain plaintext passwords and whole file bodies.
        message = "Request fields do not match the API contract"
        if request.url.path.endswith("/criteria") and any(error["type"] == "too_long" and error["loc"][-1] in {"items", "groups"} for error in exc.errors()):
            message = "A criteria version supports at most 128 conditions and 128 groups"
        return error_response(request, 422, "invalid_input", message)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, _exc: IntegrityError):
        return error_response(request, 409, "conflict", "The operation conflicts with an existing record")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, _exc: SQLAlchemyError):
        logger.error("Database operation failed; request_id=%s", request.state.request_id)
        return error_response(request, 503, "database_unavailable", "The database operation could not be completed")

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _exc: Exception):
        logger.error("Unhandled request failure; request_id=%s", request.state.request_id)
        return error_response(request, 500, "internal_error", "The request could not be completed")

    @app.get("/api/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/api/health/ready")
    def ready(db: Db):
        from .tool_records import ToolCall

        try:
            for model in (User, AuthSession, Project, Membership, FileRecord, Document, CriterionSet,
                          Asset, AssetVersion, Logo, Run, ChangeRequest, Approval, Event, Job, Outbox, ToolCall):
                db.execute(select(model.id).limit(0))
        except SQLAlchemyError:
            S.fail(503, "database_not_ready", "The database is unavailable or its schema has not been initialized")
        return {"status": "ready"}

    @app.post("/api/auth/login")
    def login(body: C.Login, request: Request, response: Response, db: Db):
        try:
            email = auth.normalize_email(body.email)
        except ValueError:
            S.fail(401, "invalid_credentials", "Email or password is incorrect")
        user = db.scalar(select(User).where(User.email == email).with_for_update())
        if user is None:
            # Same work factor for unknown accounts; no account information is returned.
            auth.verify_password(body.password, "scrypt$16384$8$1$" + "00" * 16 + "$" + "00" * 32)
            S.fail(401, "invalid_credentials", "Email or password is incorrect")
        if not user.active or user.locked_until and auth.aware(user.locked_until) > utcnow():
            S.fail(401, "invalid_credentials", "Email or password is incorrect")
        if not auth.verify_password(body.password, user.password_hash):
            user.failed_login_count += 1
            if user.failed_login_count >= 5:
                user.failed_login_count = 0
                user.locked_until = utcnow() + timedelta(minutes=5)
            S.commit(db)
            S.fail(401, "invalid_credentials", "Email or password is incorrect")
        user.failed_login_count, user.locked_until = 0, None
        old_token = request.cookies.get(settings.session_cookie)
        if old_token:
            db.execute(update(AuthSession).where(AuthSession.token_hash == auth.digest(old_token)).values(revoked_at=utcnow()))
        _session, token, csrf = auth.issue_session(db, user, settings)
        S.commit(db)
        response.set_cookie(settings.session_cookie, token, max_age=settings.session_hours * 3600,
                            httponly=True, secure=settings.cookie_secure, samesite="lax", path="/api")
        return {"user": auth.public_user(user), "csrf_token": csrf}

    @app.get("/api/auth/me")
    def me(identity: Identity):
        return {"user": auth.public_user(identity[0]), "csrf_token": identity[2]}

    @app.post("/api/auth/logout")
    def logout(response: Response, db: Db, identity: Identity):
        identity[1].revoked_at = utcnow()
        S.commit(db)
        response.delete_cookie(settings.session_cookie, path="/api", secure=settings.cookie_secure, httponly=True, samesite="lax")
        return {"ok": True}

    @app.post("/api/auth/password")
    def change_password(body: C.PasswordChange, db: Db, identity: Identity):
        user = db.scalar(select(User).where(User.id == identity[0].id).with_for_update())
        if not auth.verify_password(body.current_password, user.password_hash):
            S.fail(403, "invalid_password", "The current password is incorrect")
        user.password_hash = auth.password_hash(body.new_password)
        db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != identity[1].id,
                                             AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
        S.commit(db)
        return {"ok": True}

    @app.get("/api/admin/users")
    def users(db: Db, _admin: Admin):
        return {"items": [auth.public_user(user) for user in db.scalars(select(User).order_by(User.created_at, User.id))]}

    @app.post("/api/admin/users", status_code=201)
    def add_user(body: C.CreateUser, db: Db, _admin: Admin):
        try:
            email = auth.normalize_email(body.email)
            encoded = auth.password_hash(body.password)
        except ValueError as exc:
            S.fail(422, "invalid_user", str(exc))
        if not body.name.strip():
            S.fail(422, "invalid_user", "User name is required")
        user = User(email=email, name=body.name.strip(), password_hash=encoded, is_admin=body.is_admin)
        db.add(user)
        S.commit(db)
        return auth.public_user(user)

    @app.get("/api/projects")
    def projects(db: Db, identity: Identity):
        rows = db.execute(select(Project, Membership.role).join(Membership, Membership.project_id == Project.id)
                          .where(Membership.user_id == identity[0].id).order_by(Project.created_at.desc(), Project.id)).all()
        return {"items": [S.project_json(project, role) for project, role in rows]}

    @app.post("/api/projects", status_code=201)
    def add_project(body: C.CreateProject, db: Db, identity: Identity):
        user = identity[0]
        project = Project(id=new_id(), name=body.name, description=body.description, created_by=user.id)
        db.add(project)
        db.flush()
        db.add(Membership(project_id=project.id, user_id=user.id, role="approver"))
        S.record_event(db, project, "project.created", user.id, {"project_id": project.id})
        S.commit(db)
        return S.project_json(project, "approver")

    @app.get("/api/projects/{project_id}")
    def project_detail(project_id: str, db: Db, identity: Identity):
        project, role = S.project_access(db, project_id, identity[0])
        return S.project_json(project, role)

    @app.get("/api/projects/{project_id}/members")
    def members(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        rows = db.execute(select(Membership, User).join(User, Membership.user_id == User.id)
                          .where(Membership.project_id == project.id).order_by(User.name, User.id)).all()
        return {"items": [{"id": membership.id, "user_id": user.id, "role": membership.role, "name": user.name, "email": user.email,
                           "user": auth.public_user(user)} for membership, user in rows]}

    @app.put("/api/projects/{project_id}/members")
    def put_member(project_id: str, body: C.SetMember, db: Db, identity: Identity):
        user = identity[0]
        project, _role = S.project_access(db, project_id, user, APPROVERS, lock=True, writable=True)
        target = db.get(User, str(body.user_id))
        if target is None or not target.active:
            S.fail(404, "not_found", "User not found")
        member = db.scalar(select(Membership).where(Membership.project_id == project.id, Membership.user_id == target.id))
        if member and member.role == "approver" and body.role != "approver":
            count = db.scalar(select(func.count()).select_from(Membership).where(Membership.project_id == project.id, Membership.role == "approver"))
            if count <= 1:
                S.fail(409, "last_approver", "A project must keep at least one approver")
        if member:
            member.role = body.role
        else:
            member = Membership(project_id=project.id, user_id=target.id, role=body.role)
            db.add(member)
        S.record_event(db, project, "membership.changed", user.id, {"user_id": target.id, "role": body.role})
        S.commit(db)
        return {"id": member.id, "user_id": target.id, "role": member.role, "name": target.name, "email": target.email,
                "user": auth.public_user(target)}

    @app.post("/api/projects/{project_id}/archive")
    def archive(project_id: str, body: C.ArchiveProject, db: Db, identity: Identity):
        project, role = S.project_access(db, project_id, identity[0], APPROVERS, lock=True)
        project.archived = body.archived
        S.record_event(db, project, "project.archived" if body.archived else "project.restored", identity[0].id, {"archived": body.archived})
        S.commit(db)
        return S.project_json(project, role)

    @app.get("/api/projects/{project_id}/documents")
    def documents(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        return {"items": [S.document_json(document) for document in db.scalars(select(Document).where(Document.project_id == project.id).order_by(Document.version.desc()))]}

    @app.post("/api/projects/{project_id}/documents", status_code=201)
    async def add_document(project_id: str, db: Db, identity: Identity, file: UploadFile = File(...)):
        project, _role = S.project_access(db, project_id, identity[0], EDITORS, lock=True, writable=True)
        filename, data = await upload_bytes(file, 20 * 1024 * 1024)
        document = await run_in_threadpool(S.register_document, db, storage, project, identity[0], filename, data, settings)
        S.commit(db)
        return S.document_json(document)

    @app.get("/api/projects/{project_id}/criteria")
    def criteria(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        return {"items": [S.criteria_json(c) for c in db.scalars(select(CriterionSet).where(CriterionSet.project_id == project.id).order_by(CriterionSet.version.desc()))]}

    @app.post("/api/projects/{project_id}/criteria", status_code=201)
    def add_criteria(project_id: str, body: C.CreateCriteria, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0], EDITORS, lock=True, writable=True)
        criteria = S.create_criteria(db, project, identity[0], body)
        S.commit(db)
        return S.criteria_json(criteria)

    @app.post("/api/criteria/{criteria_id}/approve")
    def approve_criteria(criteria_id: str, db: Db, identity: Identity):
        criteria, project, _role = S.object_access(db, CriterionSet, criteria_id, identity[0], APPROVERS, lock=True, writable=True)
        S.approve_criteria(db, project, criteria, identity[0])
        S.commit(db)
        return S.criteria_json(criteria)

    @app.get("/api/projects/{project_id}/logos")
    def logos(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        return {"items": [S.logo_json(logo) for logo in db.scalars(select(Logo).where(Logo.project_id == project.id).order_by(Logo.created_at.desc(), Logo.id))]}

    @app.post("/api/projects/{project_id}/logos", status_code=201)
    async def add_logo(project_id: str, db: Db, identity: Identity, file: UploadFile = File(...), name: str = Form(...)):
        project, _role = S.project_access(db, project_id, identity[0], EDITORS, lock=True, writable=True)
        if not name.strip() or len(name.strip()) > 200:
            S.fail(422, "invalid_name", "Logo name must contain 1–200 characters")
        filename, data = await upload_bytes(file, 20 * 1024 * 1024)
        metadata = await run_in_threadpool(inspect_media, data, filename, "poster")
        record = await run_in_threadpool(S.store_file, db, storage, project, identity[0], filename, data, metadata["actual_mime"])
        logo = Logo(id=new_id(), project_id=project.id, name=name.strip(), file_id=record.id, created_by=identity[0].id)
        db.add(logo)
        S.record_event(db, project, "logo.created", identity[0].id, {"logo_id": logo.id, "file_id": record.id})
        S.commit(db)
        return S.logo_json(logo)

    @app.post("/api/logos/{logo_id}/approve")
    def approve_logo(logo_id: str, db: Db, identity: Identity):
        logo, project, _role = S.object_access(db, Logo, logo_id, identity[0], APPROVERS, lock=True, writable=True)
        if logo.status != "approved":
            if logo.status != "draft":
                S.fail(409, "invalid_state", "Logo is not awaiting approval")
            approved_count = db.scalar(select(func.count()).select_from(Logo).where(Logo.project_id == project.id, Logo.status == "approved"))
            if approved_count >= 4:
                S.fail(409, "logo_limit", "A project supports at most four approved reference logos")
            logo.status, logo.approved_by, logo.approved_at = "approved", identity[0].id, utcnow()
            db.add(Approval(project_id=project.id, target_type="logo", target_id=logo.id,
                            approved_by=identity[0].id, snapshot=S.logo_json(logo)))
            S.invalidate_assets(db, project)
            S.record_event(db, project, "logo.approved", identity[0].id, {"logo_id": logo.id})
        S.commit(db)
        return S.logo_json(logo)

    @app.get("/api/projects/{project_id}/assets")
    def assets(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        return {"items": [S.asset_json(db, asset) for asset in db.scalars(select(Asset).where(Asset.project_id == project.id).order_by(Asset.created_at.desc(), Asset.id))]}

    @app.post("/api/projects/{project_id}/assets", status_code=201)
    async def add_asset(project_id: str, db: Db, identity: Identity, file: UploadFile = File(...),
                        name: str = Form(...), kind: str = Form(...)):
        project, _role = S.project_access(db, project_id, identity[0], EDITORS, lock=True, writable=True)
        if kind not in {"poster", "video"}:
            S.fail(422, "invalid_kind", "Asset kind must be poster or video")
        filename, data = await upload_bytes(file, (100 if kind == "video" else 20) * 1024 * 1024)
        metadata = await run_in_threadpool(inspect_media, data, filename, kind)
        asset = await run_in_threadpool(S.register_asset, db, storage, project, identity[0], filename, data,
                                        name, kind, metadata, metadata["actual_mime"])
        S.commit(db)
        return S.asset_json(db, asset)

    @app.post("/api/assets/{asset_id}/versions", status_code=201)
    async def add_version(asset_id: str, db: Db, identity: Identity, file: UploadFile = File(...),
                          change_request_id: str | None = Form(default=None)):
        asset, project, _role = S.object_access(db, Asset, asset_id, identity[0], EDITORS, lock=True, writable=True)
        filename, data = await upload_bytes(file, (100 if asset.kind == "video" else 20) * 1024 * 1024)
        metadata = await run_in_threadpool(inspect_media, data, filename, asset.kind)
        version = await run_in_threadpool(S.add_asset_version, db, storage, project, asset, identity[0], filename,
                                          data, metadata, metadata["actual_mime"], change_request_id)
        S.commit(db)
        return S.version_json(version)

    @app.get("/api/files/{file_id}")
    def file_content(file_id: str, request: Request, db: Db, identity: Identity):
        file, _project, _role = S.object_access(db, FileRecord, file_id, identity[0])
        if not file.storage_key.startswith(file.project_id + "/"):
            S.fail(503, "invalid_file_record", "The stored file is not linked to its registered project")
        start, end, status = None, None, 200
        byte_range = request.headers.get("range")
        if byte_range:
            import re
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range)
            if not match or len(byte_range) > 200 or not any(match.groups()):
                raise HTTPException(416, {"code": "invalid_range", "message": "Only a single byte range is supported"}, headers={"Content-Range": f"bytes */{file.size}"})
            first, last = match.groups()
            if first:
                start, end = int(first), min(int(last), file.size - 1) if last else file.size - 1
            else:
                length = int(last)
                start, end = max(file.size - length, 0), file.size - 1
                if length == 0:
                    start = file.size
            if start >= file.size or end < start:
                raise HTTPException(416, {"code": "invalid_range", "message": "Byte range is outside this file"}, headers={"Content-Range": f"bytes */{file.size}"})
            status = 206
        try:
            data = storage.read(file.storage_key, start, end)
        except FileNotFoundError:
            S.fail(503, "file_unavailable", "The registered file is missing from storage")
        expected_length = file.size if start is None else end - start + 1
        if len(data) != expected_length:
            S.fail(503, "file_unavailable", "The stored file does not match its registered size")
        if (start is None or start == 0 and end == file.size - 1) and hashlib.sha256(data).hexdigest() != file.sha256:
            S.fail(503, "file_unavailable", "The stored file does not match its registered checksum")
        disposition = "inline" if file.media_type.startswith(("image/", "video/")) or file.media_type == "application/pdf" else "attachment"
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(len(data)),
                   "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file.filename, safe='')}",
                   "Content-Security-Policy": "sandbox"}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{file.size}"
        return Response(content=data, status_code=status, media_type=file.media_type, headers=headers)

    @app.get("/api/projects/{project_id}/runs")
    def runs(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        return {"items": [S.run_json(db, run) for run in db.scalars(select(Run).where(Run.project_id == project.id).order_by(Run.created_at.desc(), Run.id))]}

    @app.post("/api/projects/{project_id}/runs", status_code=201)
    def add_run(project_id: str, body: C.CreateRun, db: Db, identity: Identity,
                idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
        project, _role = S.project_access(db, project_id, identity[0], EDITORS, lock=True, writable=True)
        run = S.create_run(db, project, identity[0], body, idempotency_key, settings)
        S.commit(db)
        return S.run_json(db, run)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str, db: Db, identity: Identity):
        run, _project, _role = S.object_access(db, Run, run_id, identity[0])
        return S.run_json(db, run)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str, db: Db, identity: Identity):
        run, project, _role = S.object_access(db, Run, run_id, identity[0], EDITORS, lock=True, writable=True)
        S.cancel_run(db, project, run, identity[0])
        S.commit(db)
        return S.run_json(db, run)

    @app.post("/api/runs/{run_id}/retry")
    def retry_run(run_id: str, db: Db, identity: Identity,
                  idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
        run, project, _role = S.object_access(db, Run, run_id, identity[0], EDITORS, lock=True, writable=True)
        S.retry_run(db, project, run, identity[0], idempotency_key)
        S.commit(db)
        return S.run_json(db, run)

    @app.get("/api/runs/{run_id}/change-requests")
    def change_requests(run_id: str, db: Db, identity: Identity):
        run, _project, _role = S.object_access(db, Run, run_id, identity[0])
        return {"items": [S.change_json(change) for change in db.scalars(select(ChangeRequest).where(ChangeRequest.run_id == run.id).order_by(ChangeRequest.created_at, ChangeRequest.id))]}

    @app.post("/api/runs/{run_id}/change-requests", status_code=201)
    def add_change_request(run_id: str, body: C.CreateChangeRequest, db: Db, identity: Identity):
        run, project, _role = S.object_access(db, Run, run_id, identity[0], EDITORS, lock=True, writable=True)
        change = S.create_change_request(db, project, run, identity[0], body)
        S.commit(db)
        return S.change_json(change)

    @app.post("/api/change-requests/{change_id}/approve")
    def approve_change(change_id: str, db: Db, identity: Identity):
        change, project, _role = S.object_access(db, ChangeRequest, change_id, identity[0], APPROVERS, lock=True, writable=True)
        S.approve_change_request(db, project, change, identity[0])
        S.commit(db)
        return S.change_json(change)

    @app.post("/api/runs/{run_id}/approve", status_code=201)
    def approve_run(run_id: str, body: C.FinalApproval, db: Db, identity: Identity):
        run, project, _role = S.object_access(db, Run, run_id, identity[0], APPROVERS, lock=True, writable=True)
        approval = S.approve_run(db, project, run, identity[0], body)
        S.commit(db)
        return S.approval_json(approval)

    @app.get("/api/projects/{project_id}/history")
    def history(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        events = db.scalars(select(Event).where(Event.project_id == project.id).order_by(Event.created_at.desc(), Event.id)).all()
        approvals = db.scalars(select(Approval).where(Approval.project_id == project.id).order_by(Approval.created_at.desc(), Approval.id)).all()
        return {"items": [S.event_json(event) for event in events], "approvals": [S.approval_json(approval) for approval in approvals]}

    @app.get("/api/projects/{project_id}/graph")
    def graph(project_id: str, db: Db, identity: Identity):
        from .graph import GraphStore, GraphUnavailable

        project, _role = S.project_access(db, project_id, identity[0])
        graph_store = GraphStore(settings)
        try:
            result = graph_store.project_graph(project.id)
            result["status"] = project.graph_status
            result["synced_at"] = S.iso(project.graph_synced_at)
            return result
        except GraphUnavailable:
            S.fail(503, "graph_unavailable", "The relationship store is currently unavailable")
        finally:
            graph_store.close()

    @app.get("/api/projects/{project_id}/impact")
    def impact(project_id: str, criterion_id: str, db: Db, identity: Identity):
        from .graph import GraphStore, GraphUnavailable

        project, _role = S.project_access(db, project_id, identity[0])
        criterion_id = S.uuid_string(criterion_id)
        # Check ownership in the source of truth before querying a derived index.
        sets = db.scalars(select(CriterionSet).where(CriterionSet.project_id == project.id)).all()
        if not any(item.get("id") == criterion_id for criteria in sets for item in criteria.items):
            S.fail(404, "not_found", "Criterion not found")
        graph_store = GraphStore(settings)
        try:
            return graph_store.impact(project.id, criterion_id)
        except GraphUnavailable:
            S.fail(503, "graph_unavailable", "The relationship store is currently unavailable")
        finally:
            graph_store.close()

    app.add_middleware(BodyLimitMiddleware)
    return app
