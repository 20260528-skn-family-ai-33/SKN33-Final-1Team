"""Authenticated MCP tools. Inputs are database IDs, never paths or model URLs."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
import hmac
import json
from functools import wraps
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select, text
from starlette.responses import JSONResponse

from .database import create_engine, session_factory
from .models import Document, FileRecord, Job, Project, Run, new_id, utcnow
from .services import record_event
from .settings import get_settings
from .storage import create_storage
from .tool_records import ToolCall


class ToolFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class ToolService:
    def __init__(self, settings, engine=None, storage=None):
        self.settings = settings
        self.engine = engine or create_engine(settings.database_url)
        self.sessions = session_factory(self.engine)
        self.storage = storage or create_storage(settings)

    def job(self, db, job_id: str, *, active=True, lease_token=None):
        job = db.get(Job, str(UUID(job_id)))
        if job is None or job.kind not in {"document", "review"}:
            raise ToolFailure("job_not_found", "실행할 작업을 찾을 수 없습니다.")
        if active and job.state != "running":
            raise ToolFailure("job_inactive", "실행 중인 작업만 도구를 호출할 수 있습니다.")
        if lease_token is not None and job.lease_token != lease_token:
            raise ToolFailure("cancelled", "작업 실행 권한이 다른 worker로 넘어갔습니다.")
        return job

    def file(self, db, project_id, file_id):
        item = db.get(FileRecord, str(UUID(file_id)))
        if item is None or item.project_id != project_id:
            raise ToolFailure("file_not_found", "작업에 속한 파일을 찾을 수 없습니다.")
        data = self.storage.read(item.storage_key)
        if hashlib.sha256(data).hexdigest() != item.sha256:
            raise ToolFailure("file_hash_mismatch", "저장된 파일의 무결성을 확인할 수 없습니다.")
        return item, data

    @staticmethod
    def receipt(call):
        return {"call_id": call.id, "state": call.state, "result": call.result, "error": call.error}

    def cached(self, job_id, operation, ordinal, action, lease_token=None):
        with self.sessions.begin() as db:
            job = self.job(db, job_id)
            db.scalar(select(Project).where(Project.id == job.project_id).with_for_update())
            # Serialize duplicate submissions before creating a durable receipt.
            db.scalar(select(Job).where(Job.id == job.id).with_for_update().execution_options(populate_existing=True))
            if (job.state != "running" or (not self.settings.testing and not lease_token)
                    or (lease_token is not None and job.lease_token != lease_token)):
                raise ToolFailure("cancelled", "현재 작업 실행 권한이 필요합니다.")
            old = db.scalar(select(ToolCall).where(ToolCall.job_id == job.id,
                ToolCall.operation == operation, ToolCall.ordinal == str(ordinal)))
            if old:
                return self.receipt(old)
            call = ToolCall(id=new_id(), project_id=job.project_id, job_id=job.id,
                            operation=operation, ordinal=str(ordinal), state="running")
            db.add(call)
            record_event(db, db.get(Project, job.project_id), "tool.started", None,
                         {"call_id": call.id, "operation": operation, "ordinal": ordinal},
                         job.target_id if job.kind == "review" else None)
        try:
            result = action()
            state, error = "completed", None
        except Exception as exc:
            # Only bounded domain messages cross the worker boundary; never HTTP response bodies.
            from .documents import DocumentError
            from .media import MediaError
            from .qwen import QwenError
            code = getattr(exc, "code", "tool_failed")
            safe = isinstance(exc, (ToolFailure, DocumentError, MediaError, QwenError))
            state, result = "failed", None
            error = {"code": code if safe else "tool_failed",
                     "message": str(exc)[:500] if safe else "도구 실행에 실패했습니다. 실행 환경을 확인해 주세요."}
        with self.sessions.begin() as db:
            saved = db.get(ToolCall, call.id)
            saved.state, saved.result, saved.error, saved.finished_at = state, result, error, utcnow()
            job = self.job(db, job_id, active=False)
            record_event(db, db.get(Project, job.project_id), "tool.finished", None,
                         {"call_id": call.id, "operation": operation, "state": state, "error": error,
                          "usage": result.get("usage") if isinstance(result, dict) else None},
                         job.target_id if job.kind == "review" else None)
            return self.receipt(saved)

    def read_document(self, job_id, lease_token: str | None = None):
        def read():
            from .documents import extract_document
            with self.sessions() as db:
                job = self.job(db, job_id, lease_token=lease_token)
                document = db.get(Document, job.target_id) if job.kind == "document" else None
                if document is None:
                    raise ToolFailure("wrong_job_kind", "문서 추출 작업이 아닙니다.")
                item, data = self.file(db, job.project_id, document.file_id)
                return extract_document(data, item.filename)
        return self.cached(job_id, "read_document", 0, read, lease_token)

    def prepare_media(self, job_id, lease_token: str | None = None):
        def prepare():
            from .media import prepare_asset
            with self.sessions() as db:
                job = self.job(db, job_id, lease_token=lease_token)
                run = db.get(Run, job.target_id) if job.kind == "review" else None
                if run is None:
                    raise ToolFailure("wrong_job_kind", "미디어 검사 작업이 아닙니다.")
                item, data = self.file(db, job.project_id, run.snapshot["asset_version"]["file_id"])
                kind = run.snapshot["asset"]["kind"]
                reference_inputs = [self.file(db, job.project_id, logo["file_id"]) for logo in run.snapshot["logos"]]
            stored = []
            try:
                with tempfile.TemporaryDirectory(prefix="cc-media-") as folder:
                    manifest = prepare_asset(data, item.filename, kind, Path(folder))
                    references = []
                    for reference, content in reference_inputs:
                        prepared = prepare_asset(content, reference.filename, "poster", Path(folder))
                        references.append({**prepared["frames"][0], "source_file_id": reference.id, "role": "logo"})
                    frames, logo_frames = [], []
                    with self.sessions.begin() as db:
                        self.job(db, job_id, lease_token=lease_token)
                        for frame in [*manifest["frames"], *references]:
                            path = Path(frame["path"]).resolve()
                            if not path.is_relative_to(Path(folder).resolve()):
                                raise ToolFailure("invalid_derived_path", "파생 파일 경로가 올바르지 않습니다.")
                            content = path.read_bytes()
                            file_id = new_id()
                            key = f"{job.project_id}/derived/{file_id}.jpg"
                            self.storage.put(key, content, "image/jpeg")
                            stored.append(key)
                            db.add(FileRecord(id=file_id, project_id=job.project_id, storage_key=key,
                                filename=f"evidence-{file_id}.jpg", media_type="image/jpeg", size=len(content),
                                sha256=hashlib.sha256(content).hexdigest()))
                            target = logo_frames if frame.get("role") == "logo" else frames
                            target.append({k: v for k, v in frame.items() if k != "path"} | {"file_id": file_id})
                    return {**manifest, "frames": frames, "logos": logo_frames}
            except Exception:
                for key in stored:
                    self.storage.delete(key)
                raise
        return self.cached(job_id, "prepare_media", 0, prepare, lease_token)

    def manifest(self, db, job_id):
        call = db.scalar(select(ToolCall).where(ToolCall.job_id == job_id,
                            ToolCall.operation == "prepare_media", ToolCall.state == "completed"))
        if call is None:
            raise ToolFailure("media_not_ready", "미디어 전처리가 완료되지 않았습니다.")
        return call.result

    @contextmanager
    def gpu_slot(self):
        with self.engine.connect() as connection:
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(29003504)"))
            if not acquired:
                raise ToolFailure("gpu_busy", "GPU가 다른 작업을 처리하고 있습니다.")
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(29003504)"))
                connection.commit()

    def submit_inference(self, job_id, operation, batch_index=0, lease_token: str | None = None):
        if (operation not in {"extract_criteria", "inspect", "report"} or not 0 <= batch_index < 3
                or (operation != "inspect" and batch_index != 0)):
            raise ToolFailure("invalid_operation", "허용되지 않은 추론 요청입니다.")
        # Acquire before recording: a busy slot is safe to put back on the queue.
        with self.gpu_slot():
            return self.cached(job_id, operation, batch_index,
                               lambda: self.infer(job_id, operation, batch_index, lease_token), lease_token)

    def infer(self, job_id, operation, batch_index, lease_token=None):
        from .qwen import QwenClient
        from .prompts import MAX_IMAGES_PER_CALL
        with self.sessions() as db:
            job = self.job(db, job_id, lease_token=lease_token)
            run = db.get(Run, job.target_id) if job.kind == "review" else None
            profile = (run.snapshot if run else job.payload).get("inference_profile")
            if profile != self.settings.inference_profile():
                raise ToolFailure("inference_profile_changed", "모델·프롬프트·처리 코드가 실행 생성 시점과 다릅니다. 새 실행을 생성해 주세요.")
            if operation == "extract_criteria" and job.kind == "document":
                with QwenClient(self.settings.qwen_base_url, self.settings.qwen_api_key,
                                profile["model"], timeout=120, max_calls=profile["max_calls_per_stage"]) as client:
                    result = client.extract_criteria(db.get(Document, job.target_id).blocks)
                    return {**result, "usage": client.stats}
            if run is None:
                raise ToolFailure("wrong_job_kind", "추론 요청과 작업 종류가 다릅니다.")
            if operation == "report":
                with QwenClient(self.settings.qwen_base_url, self.settings.qwen_api_key,
                                profile["model"], timeout=120, max_calls=profile["max_calls_per_stage"]) as client:
                    result = client.report(run.results)
                    return {**result, "usage": client.stats}
            if operation != "inspect":
                raise ToolFailure("wrong_job_kind", "추론 요청과 작업 종류가 다릅니다.")
            manifest = self.manifest(db, job_id)
            frames = manifest["frames"]
            chosen = frames[batch_index * MAX_IMAGES_PER_CALL:(batch_index + 1) * MAX_IMAGES_PER_CALL]
            if not chosen:
                raise ToolFailure("invalid_batch", "검사할 프레임 묶음이 없습니다.")
            images, logos = [], []
            for frame in chosen:
                item, data = self.file(db, job.project_id, frame["file_id"])
                images.append({**frame, "data": data, "media_type": item.media_type, "asset_id": run.snapshot["asset"]["id"]})
            for logo in manifest.get("logos", []):
                item, data = self.file(db, job.project_id, logo["file_id"])
                logos.append({"file_id": item.id, "data": data, "media_type": item.media_type,
                              "frame_ms": None, "role": "logo"})
            with QwenClient(self.settings.qwen_base_url, self.settings.qwen_api_key,
                            profile["model"], timeout=120, max_calls=profile["max_calls_per_stage"]) as client:
                result = client.inspect(run.snapshot["items"], images, logos=logos)
                return {**result, "usage": client.stats}

    def get_inference(self, job_id, call_id):
        with self.sessions() as db:
            job = self.job(db, job_id, active=False)
            call = db.get(ToolCall, str(UUID(call_id)))
            if call is None or call.job_id != job.id:
                raise ToolFailure("call_not_found", "이 작업의 호출 기록을 찾을 수 없습니다.")
            return self.receipt(call)

    def read_evidence(self, job_id, file_id):
        with self.sessions() as db:
            job = self.job(db, job_id, active=False)
            manifest = self.manifest(db, job.id)
            frame = next((f for f in [*manifest["frames"], *manifest.get("logos", [])] if f["file_id"] == file_id), None)
            if frame is None:
                raise ToolFailure("evidence_not_found", "이 실행에서 생성한 근거가 아닙니다.")
            item, _ = self.file(db, job.project_id, file_id)
            return {**frame, "sha256": item.sha256, "size": item.size}


class BearerMiddleware:
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            supplied = dict(scope.get("headers", [])).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, b"Bearer " + self.token.encode()):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app():
    settings = get_settings()
    if len(settings.mcp_token) < 32:
        raise RuntimeError("CC_MCP_TOKEN must contain at least 32 characters")
    host = urlsplit(settings.mcp_url).hostname or "127.0.0.1"
    server = FastMCP("creative-checker", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=[host, f"{host}:*", "127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"], allowed_origins=[]))
    service = ToolService(settings)
    def boundary(method):
        @wraps(method)
        def invoke(*args, **kwargs):
            try:
                return method(*args, **kwargs)
            except ToolFailure as exc:
                return {"state": "failed", "error": {"code": exc.code, "message": str(exc)}}
        return invoke

    for name in ("read_document", "prepare_media", "submit_inference", "get_inference", "read_evidence"):
        # Only these named capabilities are exposed, with no sampling or client roots.
        server.add_tool(boundary(getattr(service, name)), name=name)
    return BearerMiddleware(server.streamable_http_app(), settings.mcp_token)


async def call_tool(settings, name, arguments):
    if not settings.mcp_url or len(settings.mcp_token) < 32:
        raise ToolFailure("mcp_not_configured", "MCP 연결 설정이 필요합니다.")
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {settings.mcp_token}"},
                                 timeout=httpx.Timeout(660, connect=10), follow_redirects=False) as http:
        async with streamable_http_client(settings.mcp_url, http_client=http) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=660)) as session:
                await session.initialize()
                response = await session.call_tool(name, arguments)
                if response.isError:
                    # SDK tool error text may contain internals; do not persist it.
                    raise ToolFailure("mcp_tool_failed", "MCP 도구 실행에 실패했습니다.")
                if response.structuredContent is not None:
                    return response.structuredContent
                for content in response.content:
                    if content.type == "text":
                        return json.loads(content.text)
                raise ToolFailure("mcp_invalid_response", "MCP 응답에 결과가 없습니다.")
