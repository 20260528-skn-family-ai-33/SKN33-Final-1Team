"""Product runs operations."""

from copy import deepcopy
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.creative_checker import contracts as C
from backend.creative_checker.models import (
    Approval,
    Asset,
    AssetVersion,
    ChangeRequest,
    CriterionSet,
    Document,
    FileRecord,
    Job,
    Logo,
    Project,
    Run,
    User,
    new_id,
)
from backend.creative_checker.services.common import fail
from backend.creative_checker.services.events import record_event
from backend.creative_checker.services.serialization import criteria_json, logo_json, version_json


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
