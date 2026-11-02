"""Transactional product operations shared by the HTTP handlers."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import contracts as C
from .models import (
    Approval, Asset, AssetVersion, ChangeRequest, CriterionSet, Document, Event,
    FileRecord, Job, Logo, Membership, Outbox, Project, Run, User, new_id, utcnow,
)


def fail(status: int, code: str, message: str):
    raise HTTPException(status, {"code": code, "message": message})


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def uuid_string(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        fail(422, "invalid_id", "ID must be a UUID")


def project_access(db: Session, project_id: str, user: User, roles=None, *, lock=False, writable=False) -> tuple[Project, str]:
    project_id = uuid_string(project_id)
    stmt = select(Project).join(Membership, Membership.project_id == Project.id).where(
        Project.id == project_id, Membership.user_id == user.id,
    )
    if lock:
        stmt = stmt.with_for_update(of=Project)
    project = db.scalar(stmt)
    if project is None:
        fail(404, "not_found", "Project not found")
    role = db.scalar(select(Membership.role).where(Membership.project_id == project_id, Membership.user_id == user.id))
    if roles and role not in roles:
        fail(403, "forbidden", "Your project role does not permit this action")
    if writable and project.archived:
        fail(409, "project_archived", "Archived projects are read only")
    return project, role


def object_access(db: Session, model, object_id: str, user: User, roles=None, *, lock=False, writable=False):
    obj = db.get(model, uuid_string(object_id))
    if obj is None:
        fail(404, "not_found", "Object not found")
    project, role = project_access(db, obj.project_id, user, roles, lock=lock, writable=writable)
    if lock:
        obj = db.scalar(select(model).where(model.id == obj.id).with_for_update().execution_options(populate_existing=True))
    return obj, project, role


def project_json(project: Project, role: str) -> dict:
    return {"id": project.id, "name": project.name, "description": project.description,
            "role": role, "archived": project.archived,
            "current_document_id": project.current_document_id, "current_criteria_id": project.current_criteria_id,
            "graph_status": project.graph_status, "graph_synced_at": iso(project.graph_synced_at),
            "graph_error": project.graph_error, "graph_revision": project.graph_revision,
            "created_at": iso(project.created_at), "updated_at": iso(project.updated_at)}


def document_json(document: Document) -> dict:
    return {"id": document.id, "project_id": document.project_id, "version": document.version,
            "filename": document.filename, "file_id": document.file_id, "status": document.status,
            "blocks": document.blocks, "criteria": document.criteria, "groups": document.groups, "errors": document.errors,
            "created_at": iso(document.created_at)}


def criteria_json(criteria: CriterionSet) -> dict:
    return {"id": criteria.id, "project_id": criteria.project_id, "version": criteria.version,
            "status": criteria.status, "document_id": criteria.document_id, "items": criteria.items,
            "groups": criteria.groups, "approved_by": criteria.approved_by,
            "approved_at": iso(criteria.approved_at), "created_at": iso(criteria.created_at)}


def version_json(version: AssetVersion) -> dict:
    return {"id": version.id, "version": version.version, "file_id": version.file_id,
            "filename": version.filename, "metadata": version.media_metadata,
            "change_request_id": version.change_request_id, "created_at": iso(version.created_at)}


def asset_json(db: Session, asset: Asset) -> dict:
    versions = db.scalars(select(AssetVersion).where(AssetVersion.asset_id == asset.id).order_by(AssetVersion.version)).all()
    return {"id": asset.id, "project_id": asset.project_id, "name": asset.name, "kind": asset.kind,
            "versions": [version_json(v) for v in versions], "needs_review": asset.needs_review,
            "created_at": iso(asset.created_at)}


def logo_json(logo: Logo) -> dict:
    return {"id": logo.id, "project_id": logo.project_id, "name": logo.name,
            "file_id": logo.file_id, "version": logo.version, "status": logo.status,
            "approved_by": logo.approved_by, "approved_at": iso(logo.approved_at), "created_at": iso(logo.created_at)}


def event_json(event: Event) -> dict:
    return {"id": event.id, "project_id": event.project_id, "run_id": event.run_id,
            "kind": event.kind, "actor_id": event.actor_id, "payload": event.payload, "created_at": iso(event.created_at)}


def approval_json(approval: Approval) -> dict:
    return {"id": approval.id, "project_id": approval.project_id, "run_id": approval.run_id,
            "target_type": approval.target_type, "target_id": approval.target_id, "approved_by": approval.approved_by,
            "exception_reason": approval.exception_reason, "exception_criterion_ids": approval.exception_criterion_ids,
            "original_verdict": approval.original_verdict, "snapshot": approval.snapshot, "created_at": iso(approval.created_at)}


def change_json(change: ChangeRequest) -> dict:
    return {"id": change.id, "project_id": change.project_id, "run_id": change.run_id,
            "items": change.items, "message": change.message, "status": change.status,
            "created_by": change.created_by, "approved_by": change.approved_by,
            "approved_at": iso(change.approved_at), "created_at": iso(change.created_at)}


def run_json(db: Session, run: Run) -> dict:
    version = db.get(AssetVersion, run.asset_version_id)
    events = db.scalars(select(Event).where(Event.run_id == run.id).order_by(Event.created_at, Event.id)).all()
    approval = db.scalar(select(Approval).where(Approval.run_id == run.id))
    return {"id": run.id, "project_id": run.project_id, "asset_id": version.asset_id,
            "asset_version_id": run.asset_version_id, "criteria_version_id": run.criteria_version_id,
            "previous_run_id": run.previous_run_id, "state": run.state, "stage": run.stage,
            "verdict": run.verdict, "progress": run.progress, "results": run.results,
            "report": run.report, "errors": run.errors, "events": [event_json(e) for e in events],
            "snapshot": run.snapshot, "attempt": run.attempt, "approval": approval_json(approval) if approval else None,
            "created_at": iso(run.created_at), "updated_at": iso(run.updated_at)}


def touch_project(db: Session, project: Project | str) -> int:
    project_id = project.id if isinstance(project, Project) else project
    return db.execute(update(Project).where(Project.id == project_id).values(
        graph_revision=Project.graph_revision + 1, graph_status="pending", graph_error=None,
    ).returning(Project.graph_revision)).scalar_one()


def record_event(db: Session, project: Project, kind: str, actor_id: str | None, payload: dict,
                 run_id: str | None = None) -> Event:
    revision = touch_project(db, project)
    event = Event(id=new_id(), project_id=project.id, run_id=run_id, kind=kind,
                  actor_id=actor_id, payload=deepcopy(payload), created_at=utcnow())
    db.add(event)
    db.add(Outbox(project_id=project.id, payload={**event_json(event), "graph_revision": revision}))
    return event


def invalidate_assets(db: Session, project: Project) -> None:
    db.execute(update(Asset).where(Asset.project_id == project.id).values(needs_review=True))


def next_version(db: Session, model, parent_column, parent_id: str) -> int:
    return (db.scalar(select(func.max(model.version)).where(parent_column == parent_id)) or 0) + 1


def clean_filename(filename: str | None) -> str:
    if not filename or len(filename) > 255 or filename in {".", ".."} or any(c in filename for c in ("/", "\\", ":", "\x00", "\r", "\n")):
        fail(422, "invalid_filename", "A plain filename without a path is required")
    if any(ord(c) < 32 or ord(c) == 127 for c in filename):
        fail(422, "invalid_filename", "Filename contains control characters")
    return filename


def validate_document(data: bytes, filename: str) -> str:
    """Check the actual container; text extraction remains the document job's work."""
    import zipfile

    if not data or len(data) > 20 * 1024 * 1024:
        fail(422, "document_size_limit", "Documents must contain between 1 byte and 20MiB")
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            fail(422, "invalid_pdf", "The uploaded file is not a PDF")
        import pymupdf
        try:
            with pymupdf.open(stream=data, filetype="pdf") as pdf:
                if pdf.needs_pass or pdf.is_encrypted or not 1 <= pdf.page_count <= 50:
                    fail(422, "invalid_pdf", "PDF must be unencrypted and contain 1–50 pages")
        except HTTPException:
            raise
        except Exception:
            fail(422, "invalid_pdf", "The PDF container could not be read")
        return "application/pdf"
    if suffix not in {".docx", ".hwpx"}:
        fail(422, "unsupported_document", "PDF, DOCX and HWPX documents are supported")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2048 or sum(x.file_size for x in entries) > 20 * 1024 * 1024:
                fail(422, "invalid_archive", "Document archive exceeds its extraction limit")
            names = set()
            for entry in entries:
                parts = entry.filename.rstrip("/").split("/")
                if entry.flag_bits & 1 or any(p in {"", ".", ".."} for p in parts) or any(c in entry.filename for c in ("\\", ":", "\x00")):
                    fail(422, "invalid_archive", "Document archive contains an encrypted or unsafe member")
                if entry.filename.casefold() in names or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    fail(422, "invalid_archive", "Document archive contains duplicate or linked members")
                names.add(entry.filename.casefold())
            if suffix == ".docx" and not {"word/document.xml", "[Content_Types].xml"}.issubset(archive.namelist()):
                fail(422, "invalid_docx", "DOCX required members are missing")
            if suffix == ".hwpx" and ("Contents/content.hpf" not in archive.namelist() or "mimetype" not in archive.namelist()
                                       or archive.read("mimetype") != b"application/hwp+zip"):
                fail(422, "invalid_hwpx", "HWPX required members are missing")
    except HTTPException:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError):
        fail(422, "invalid_archive", "The document archive could not be read")
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if suffix == ".docx" else "application/hwp+zip"


def store_file(db: Session, storage, project: Project, user: User, filename: str, data: bytes, media_type: str) -> FileRecord:
    file = FileRecord(id=new_id(), project_id=project.id, storage_key=f"{project.id}/{new_id()}",
                      media_type=media_type, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                      filename=filename, created_by=user.id)
    storage.put(file.storage_key, data, media_type)
    db.info.setdefault("pending_files", []).append((storage, file.storage_key))
    db.add(file)
    db.flush()
    return file


def cleanup_files(db: Session) -> None:
    for storage, key in db.info.pop("pending_files", []):
        try:
            storage.delete(key)
        except Exception:
            # A failed storage cleanup must not hide the original transaction error.
            import logging
            logging.getLogger(__name__).error("Uncommitted file cleanup failed")


def commit(db: Session) -> None:
    db.commit()
    db.info.pop("pending_files", None)


def register_document(db: Session, storage, project: Project, user: User, filename: str, data: bytes, settings) -> Document:
    media_type = validate_document(data, filename)
    file = store_file(db, storage, project, user, filename, data, media_type)
    document = Document(id=new_id(), project_id=project.id, file_id=file.id, filename=filename,
                        version=next_version(db, Document, Document.project_id, project.id), created_by=user.id)
    db.add(document)
    db.flush()
    project.current_document_id = document.id
    project.current_criteria_id = None
    invalidate_assets(db, project)
    db.add(Job(kind="document", project_id=project.id, target_id=document.id,
               payload={"document_id": document.id, "inference_profile": settings.inference_profile()}))
    record_event(db, project, "document.submitted", user.id, {"document_id": document.id, "file_id": file.id})
    return document


def create_criteria(db: Session, project: Project, user: User, body: C.CreateCriteria) -> CriterionSet:
    document = db.get(Document, str(body.document_id))
    if document is None or document.project_id != project.id:
        fail(404, "not_found", "Document not found")
    if document.id != project.current_document_id:
        fail(409, "stale_document", "Criteria must reference the current document")
    if document.status not in {"completed", "ready", "waiting_review"}:
        fail(409, "document_not_ready", "Document extraction must finish before criteria are submitted")
    items = [item.model_dump(mode="json") for item in body.items]
    groups = [group.model_dump(mode="json") for group in body.groups]
    try:
        json.dumps([items, groups], allow_nan=False)
    except (ValueError, TypeError):
        fail(422, "invalid_json", "Criteria and groups must contain finite JSON values")
    item_ids = {item["id"] for item in items}
    if len(item_ids) != len(items) or len({g["id"] for g in groups}) != len(groups):
        fail(422, "duplicate_id", "Criterion and group IDs must be unique")
    blocks = {str(block["id"]): block for block in document.blocks if "id" in block}
    assets = set(db.scalars(select(Asset.id).where(Asset.project_id == project.id)).all())
    for item in items:
        if not set(item["asset_ids"]).issubset(assets):
            fail(404, "not_found", "Criterion asset not found")
        source = item["source"]
        if source["kind"] == "document":
            block = blocks.get(source["block_id"])
            if block is None or source["page"] != block.get("page") or source["quote"] not in block.get("text", "") or not source["quote"]:
                fail(422, "invalid_source", "A document source must quote an existing block on its actual page")
        if item["kind"] == "unsupported":
            item["supported"] = False
    for group in groups:
        if len(set(group["criterion_ids"])) != len(group["criterion_ids"]) or not set(group["criterion_ids"]).issubset(item_ids):
            fail(422, "invalid_group", "Groups must reference distinct criteria in this version")
    criteria = CriterionSet(id=new_id(), project_id=project.id, document_id=document.id,
                           version=next_version(db, CriterionSet, CriterionSet.project_id, project.id),
                           items=items, groups=groups, created_by=user.id)
    db.add(criteria)
    db.flush()
    project.current_criteria_id = criteria.id
    invalidate_assets(db, project)
    record_event(db, project, "criteria.created", user.id, {"criteria_version_id": criteria.id, "document_id": document.id})
    return criteria


def approve_criteria(db: Session, project: Project, criteria: CriterionSet, user: User) -> CriterionSet:
    if criteria.id != project.current_criteria_id or criteria.document_id != project.current_document_id:
        fail(409, "stale_criteria", "Only the current criteria for the current document may be approved")
    if criteria.status == "approved":
        return criteria
    if criteria.status != "draft":
        fail(409, "invalid_state", "Criteria are not awaiting approval")
    criteria.status, criteria.approved_by, criteria.approved_at = "approved", user.id, utcnow()
    db.add(Approval(project_id=project.id, target_type="criteria", target_id=criteria.id,
                    approved_by=user.id, snapshot=criteria_json(criteria)))
    db.add(Job(kind="approval", project_id=project.id, target_id=criteria.document_id,
               payload={"decision": "criteria_approved", "entity_id": criteria.id, "run_id": None,
                        "document_id": criteria.document_id, "criteria_version_id": criteria.id}))
    invalidate_assets(db, project)
    record_event(db, project, "criteria.approved", user.id, {"criteria_version_id": criteria.id})
    return criteria


def register_asset(db: Session, storage, project: Project, user: User, filename: str, data: bytes,
                   name: str, kind: str, metadata: dict, media_type: str) -> Asset:
    if kind not in {"poster", "video"} or not name.strip() or len(name.strip()) > 200:
        fail(422, "invalid_asset", "Asset name and poster/video kind are required")
    asset = Asset(id=new_id(), project_id=project.id, name=name.strip(), kind=kind, created_by=user.id)
    db.add(asset)
    db.flush()
    add_asset_version(db, storage, project, asset, user, filename, data, metadata, media_type)
    return asset


def add_asset_version(db: Session, storage, project: Project, asset: Asset, user: User, filename: str,
                      data: bytes, metadata: dict, media_type: str, change_request_id: str | None = None) -> AssetVersion:
    change = None
    if change_request_id:
        change = db.get(ChangeRequest, uuid_string(change_request_id))
        if change is None or change.project_id != project.id:
            fail(404, "not_found", "Change request not found")
        source_run = db.get(Run, change.run_id)
        source_version = db.get(AssetVersion, source_run.asset_version_id)
        if source_version.asset_id != asset.id:
            fail(404, "not_found", "Change request does not belong to this asset")
        if change.status != "approved":
            fail(409, "unapproved_change_request", "The change request must be approved before resubmission")
    file = store_file(db, storage, project, user, filename, data, media_type)
    version = AssetVersion(id=new_id(), project_id=project.id, asset_id=asset.id,
                           version=next_version(db, AssetVersion, AssetVersion.asset_id, asset.id),
                           file_id=file.id, filename=filename, media_metadata=deepcopy(metadata),
                           change_request_id=change.id if change else None, created_by=user.id)
    db.add(version)
    db.flush()
    asset.needs_review = True
    record_event(db, project, "asset.version_created", user.id, {"asset_id": asset.id, "asset_version_id": version.id,
                 "file_id": file.id, "change_request_id": version.change_request_id})
    return version


def request_fingerprint(operation: str, user_id: str, body: dict) -> str:
    return hashlib.sha256(json.dumps({"operation": operation, "user_id": user_id, "body": body},
                                    sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def idempotent_job(db: Session, project_id: str, key: str | None, fingerprint: str) -> Job | None:
    if not key or not 1 <= len(key) <= 200 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        fail(422, "idempotency_key_required", "A printable Idempotency-Key of at most 200 characters is required")
    job = db.scalar(select(Job).where(Job.project_id == project_id, Job.idempotency_key == key))
    if job and job.request_hash != fingerprint:
        fail(409, "idempotency_collision", "This Idempotency-Key was used with different input or by another user")
    return job


def approved_logos(db: Session, project_id: str) -> list[dict]:
    logos = db.scalars(select(Logo).where(Logo.project_id == project_id, Logo.status == "approved").order_by(Logo.id)).all()
    result = []
    for logo in logos:
        file = db.get(FileRecord, logo.file_id)
        result.append({**logo_json(logo), "sha256": file.sha256, "storage_key": file.storage_key, "media_type": file.media_type})
    return result


def create_run(db: Session, project: Project, user: User, body: C.CreateRun, key: str | None, settings) -> Run:
    fingerprint = request_fingerprint("run.create", user.id, body.model_dump(mode="json"))
    existing = idempotent_job(db, project.id, key, fingerprint)
    if existing:
        return db.get(Run, existing.target_id)
    version = db.get(AssetVersion, str(body.asset_version_id))
    if version is None or version.project_id != project.id:
        fail(404, "not_found", "Asset version not found")
    asset = db.get(Asset, version.asset_id)
    criteria_id = str(body.criteria_version_id) if body.criteria_version_id else project.current_criteria_id
    criteria = db.get(CriterionSet, criteria_id) if criteria_id else None
    if criteria is not None and criteria.project_id != project.id:
        fail(404, "not_found", "Criteria version not found")
    if criteria is None or criteria.id != project.current_criteria_id or criteria.document_id != project.current_document_id or criteria.status != "approved":
        fail(409, "approved_criteria_required", "The current document criteria must be approved before review")
    previous_run_id = str(body.previous_run_id) if body.previous_run_id else None
    if version.change_request_id:
        change = db.get(ChangeRequest, version.change_request_id)
        if previous_run_id and previous_run_id != change.run_id:
            fail(409, "change_request_mismatch", "Previous run must match the approved change request")
        previous_run_id = change.run_id
    if previous_run_id:
        previous = db.get(Run, previous_run_id)
        if previous is None or previous.project_id != project.id:
            fail(404, "not_found", "Previous run not found")
        old_version = db.get(AssetVersion, previous.asset_version_id)
        if old_version.asset_id != version.asset_id or old_version.version > version.version:
            fail(409, "unrelated_previous_run", "Previous run must belong to the same asset and an earlier or equal version")
        if previous.state in {"queued", "running", "cancel_requested"}:
            fail(409, "previous_run_active", "Previous review has not finished")
    applicable = [deepcopy(item) for item in criteria.items
                  if item.get("target", "all") in {"all", asset.kind} and (not item.get("asset_ids") or asset.id in item["asset_ids"])]
    if not applicable:
        fail(409, "no_applicable_criteria", "The approved criteria contain no conditions applicable to this asset")
    file = db.get(FileRecord, version.file_id)
    document = db.get(Document, criteria.document_id)
    document_file = db.get(FileRecord, document.file_id)
    logos = approved_logos(db, project.id)
    if len(logos) > 4:
        fail(409, "logo_limit", "A review supports at most four approved reference logos")
    snapshot = {
        "inference_profile": settings.inference_profile(),
        "asset": {"id": asset.id, "kind": asset.kind, "name": asset.name},
        "asset_version": {**version_json(version), "asset_id": asset.id, "kind": asset.kind,
                          "sha256": file.sha256, "storage_key": file.storage_key, "media_type": file.media_type},
        "criteria": criteria_json(criteria), "criteria_version_id": criteria.id, "document_id": criteria.document_id,
        "document": {"id": document.id, "version": document.version, "file_id": document.file_id,
                     "filename": document.filename, "sha256": document_file.sha256},
        "items": applicable, "groups": deepcopy(criteria.groups), "logos": logos,
        "previous_run_id": previous_run_id, "change_request_id": version.change_request_id,
    }
    run = Run(id=new_id(), project_id=project.id, asset_version_id=version.id, criteria_version_id=criteria.id,
              previous_run_id=previous_run_id, snapshot=snapshot, created_by=user.id,
              progress={"completed": 0, "total": len(applicable)})
    db.add(run)
    db.flush()
    db.add(Job(kind="review", project_id=project.id, target_id=run.id, attempt=1,
               payload={"run_id": run.id, "attempt": 1}, idempotency_key=key, request_hash=fingerprint))
    asset.needs_review = True
    record_event(db, project, "run.submitted", user.id, {"run_id": run.id, "attempt": 1}, run.id)
    return run


def cancel_run(db: Session, project: Project, run: Run, user: User) -> Run:
    if run.state in {"cancelled", "cancel_requested"}:
        return run
    if run.state not in {"queued", "running"}:
        fail(409, "invalid_state", "Only queued or running reviews can be cancelled")
    jobs = db.scalars(select(Job).where(Job.target_id == run.id, Job.project_id == project.id,
                                      Job.kind == "review", Job.attempt == run.attempt).with_for_update()).all()
    active = False
    for job in jobs:
        if job.state == "running":
            job.state = "cancel_requested"
            active = True
        elif job.state == "queued":
            job.state = "cancelled"
    run.state = "cancel_requested" if active else "cancelled"
    run.stage = run.state
    record_event(db, project, "run.cancel_requested", user.id, {"state": run.state, "attempt": run.attempt}, run.id)
    return run


def retry_run(db: Session, project: Project, run: Run, user: User, key: str | None) -> Run:
    fingerprint = request_fingerprint("run.retry", user.id, {"run_id": run.id})
    if idempotent_job(db, project.id, key, fingerprint):
        return run
    if run.state not in {"failed", "cancelled"}:
        fail(409, "invalid_state", "Only failed or cancelled reviews can be retried")
    if db.scalar(select(Approval.id).where(Approval.run_id == run.id)):
        fail(409, "already_approved", "Create a new review instead of retrying a run that received final approval")
    jobs = db.scalars(select(Job).where(Job.project_id == project.id, Job.target_id == run.id,
                                      Job.state.in_(["running", "cancel_requested"])).with_for_update()).all()
    if jobs:
        fail(409, "job_still_active", "The previous review job is still active")
    record_event(db, project, "run.attempt_finished", user.id,
                 {"attempt": run.attempt, "state": run.state, "verdict": run.verdict, "results": run.results,
                  "report": run.report, "errors": run.errors, "progress": run.progress}, run.id)
    run.attempt += 1
    run.state, run.stage, run.verdict = "queued", "queued", None
    run.progress, run.results, run.report, run.errors = {"completed": 0, "total": len(run.snapshot["items"])}, [], None, []
    db.add(Job(kind="review", project_id=project.id, target_id=run.id, attempt=run.attempt,
               payload={"run_id": run.id, "attempt": run.attempt}, idempotency_key=key, request_hash=fingerprint))
    record_event(db, project, "run.retried", user.id, {"attempt": run.attempt}, run.id)
    return run


def create_change_request(db: Session, project: Project, run: Run, user: User, body: C.CreateChangeRequest) -> ChangeRequest:
    if run.state not in {"waiting_review", "completed"}:
        fail(409, "review_not_finished", "A completed inspection is required before requesting changes")
    if db.scalar(select(Approval.id).where(Approval.run_id == run.id)):
        fail(409, "already_approved", "This run has already received final approval")
    ids = {item["id"] for item in run.snapshot["items"]}
    supplied = [str(item.criterion_id) for item in body.items]
    if len(set(supplied)) != len(supplied) or not set(supplied).issubset(ids):
        fail(422, "invalid_criteria", "Change requests must reference distinct criteria inspected in this run")
    change = ChangeRequest(id=new_id(), project_id=project.id, run_id=run.id,
                           items=[item.model_dump(mode="json") for item in body.items], message=body.message, created_by=user.id)
    db.add(change)
    db.flush()
    record_event(db, project, "change_request.created", user.id, {"change_request_id": change.id}, run.id)
    return change


def approve_change_request(db: Session, project: Project, change: ChangeRequest, user: User) -> ChangeRequest:
    if change.status == "approved":
        return change
    if change.status != "draft":
        fail(409, "invalid_state", "Change request is not awaiting approval")
    if db.scalar(select(Approval.id).where(Approval.run_id == change.run_id)):
        fail(409, "already_approved", "The source review has already received final approval")
    change.status, change.approved_by, change.approved_at = "approved", user.id, utcnow()
    db.add(Approval(project_id=project.id, target_type="change_request", target_id=change.id,
                    approved_by=user.id, snapshot=change_json(change)))
    run = db.get(Run, change.run_id)
    db.add(Job(kind="approval", project_id=project.id, target_id=change.run_id, attempt=run.attempt,
               payload={"decision": "changes_approved", "entity_id": change.id, "run_id": change.run_id, "attempt": run.attempt}))
    record_event(db, project, "change_request.approved", user.id, {"change_request_id": change.id}, change.run_id)
    return change


def approve_run(db: Session, project: Project, run: Run, user: User, body: C.FinalApproval) -> Approval:
    if db.scalar(select(Approval.id).where(Approval.run_id == run.id)):
        fail(409, "already_approved", "This run has already received final approval")
    if run.state not in {"waiting_review", "completed", "failed", "cancelled"}:
        fail(409, "review_not_finished", "Active executions cannot receive final approval")
    version = db.get(AssetVersion, run.asset_version_id)
    latest = db.scalar(select(func.max(AssetVersion.version)).where(AssetVersion.asset_id == version.asset_id))
    if version.version != latest or run.criteria_version_id != project.current_criteria_id or run.snapshot["document_id"] != project.current_document_id:
        fail(409, "stale_review", "The latest asset and current approved criteria must be inspected before final approval")
    if [(x["id"], x["file_id"]) for x in run.snapshot["logos"]] != [(x["id"], x["file_id"]) for x in approved_logos(db, project.id)]:
        fail(409, "stale_logos", "Approved reference logos changed; inspect the asset again")
    pending_change = db.scalar(select(ChangeRequest.id).where(ChangeRequest.run_id == run.id,
                                                              ChangeRequest.status.in_(["draft", "approved"])))
    if pending_change:
        fail(409, "changes_requested", "Submit a new asset version and complete reinspection before final approval")
    applicable = {item["id"]: item for item in run.snapshot["items"]}
    results = {result.get("criterion_id"): result for result in run.results}
    if len(results) != len(run.results) or not set(results).issubset(applicable):
        fail(409, "invalid_results", "Inspection results contain duplicate or unrelated criterion IDs")
    exceptions = {str(item_id) for item_id in body.exception_criterion_ids}
    if len(exceptions) != len(body.exception_criterion_ids) or not exceptions.issubset(applicable):
        fail(422, "invalid_exception", "Exceptions must reference distinct criteria in this inspection")
    def passed(key):
        result = results.get(key, {})
        return result.get("execution_status") == "completed" and result.get("verdict") == "pass"

    needed = {key for key, item in applicable.items() if item.get("required", True) and not passed(key)}
    for group in run.snapshot.get("groups", []):
        group_ids = set(group.get("criterion_ids", [])) & applicable.keys()
        if group.get("condition") or group.get("exception"):
            needed.update(group_ids)
        elif group.get("operator") == "any" and any(passed(key) for key in group_ids):
            needed.difference_update(group_ids)
    if run.verdict != "pass" and not needed:
        needed = {key for key in applicable if not passed(key)}
    reason = body.exception_reason.strip() if body.exception_reason else None
    if not needed.issubset(exceptions) or ((exceptions or run.state in {"failed", "cancelled"} or run.verdict != "pass") and not reason):
        fail(409, "exception_required", "Non-passing required criteria and unresolved groups require explicit exception targets and a reason")
    approval = Approval(id=new_id(), project_id=project.id, run_id=run.id, target_type="run", target_id=run.id,
                        approved_by=user.id, exception_reason=reason,
                        exception_criterion_ids=sorted(exceptions), original_verdict=run.verdict,
                        snapshot={"input": deepcopy(run.snapshot), "results": deepcopy(run.results),
                                  "report": deepcopy(run.report), "state": run.state, "attempt": run.attempt,
                                  "errors": deepcopy(run.errors), "verdict": run.verdict})
    db.add(approval)
    db.add(Job(kind="approval", project_id=project.id, target_id=run.id, attempt=run.attempt,
               payload={"decision": "final_approved", "entity_id": approval.id, "run_id": run.id, "attempt": run.attempt}))
    db.get(Asset, version.asset_id).needs_review = False
    record_event(db, project, "run.approved", user.id, {"approval_id": approval.id,
                 "exception_criterion_ids": sorted(exceptions), "original_verdict": run.verdict}, run.id)
    # Human approval never rewrites the execution state or machine verdict.
    return approval
