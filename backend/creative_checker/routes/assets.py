"""HTTP routes for assets."""

import hashlib
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from backend.creative_checker import services as S
from backend.creative_checker.http import (
    APPROVERS,
    Db,
    EDITORS,
    Identity,
    inspect_media,
    upload_bytes,
)
from backend.creative_checker.models import Approval, Asset, FileRecord, Logo, new_id, utcnow


def create_router(storage):
    router = APIRouter()

    @router.get("/api/projects/{project_id}/logos")
    def logos(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        return {"items": [S.logo_json(logo) for logo in db.scalars(select(Logo).where(Logo.project_id == project.id).order_by(Logo.created_at.desc(), Logo.id))]}

    @router.post("/api/projects/{project_id}/logos", status_code=201)
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

    @router.post("/api/logos/{logo_id}/approve")
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

    @router.get("/api/projects/{project_id}/assets")
    def assets(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        return {"items": [S.asset_json(db, asset) for asset in db.scalars(select(Asset).where(Asset.project_id == project.id).order_by(Asset.created_at.desc(), Asset.id))]}

    @router.post("/api/projects/{project_id}/assets", status_code=201)
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

    @router.post("/api/assets/{asset_id}/versions", status_code=201)
    async def add_version(asset_id: str, db: Db, identity: Identity, file: UploadFile = File(...),
                          change_request_id: str | None = Form(default=None)):
        asset, project, _role = S.object_access(db, Asset, asset_id, identity[0], EDITORS, lock=True, writable=True)
        filename, data = await upload_bytes(file, (100 if asset.kind == "video" else 20) * 1024 * 1024)
        metadata = await run_in_threadpool(inspect_media, data, filename, asset.kind)
        version = await run_in_threadpool(S.add_asset_version, db, storage, project, asset, identity[0], filename,
                                          data, metadata, metadata["actual_mime"], change_request_id)
        S.commit(db)
        return S.version_json(version)

    @router.get("/api/files/{file_id}")
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

    return router
