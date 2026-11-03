"""The rebuilt FastAPI entry point. No model is loaded by the HTTP process."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.creative_checker import auth
from backend.creative_checker.database import create_engine, session_factory
from backend.creative_checker.http import BodyLimitMiddleware, error_response
from backend.creative_checker.models import new_id
from backend.creative_checker.routes import (
    approvals,
    assets,
    documents,
    graph,
    health,
    projects,
    runs,
)
from backend.creative_checker.routes import auth as auth_routes
from backend.creative_checker.settings import Settings, get_settings
from backend.creative_checker.storage import create_storage

logger = logging.getLogger(__name__)


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

    app.include_router(health.create_router())
    app.include_router(auth_routes.create_router(settings))
    app.include_router(projects.create_router())
    app.include_router(documents.create_router(settings, storage))
    app.include_router(assets.create_router(storage))
    app.include_router(runs.create_router(settings))
    app.include_router(approvals.create_router())
    app.include_router(graph.create_router(settings))

    app.add_middleware(BodyLimitMiddleware)
    return app
