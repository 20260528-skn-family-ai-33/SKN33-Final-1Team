"""Product documents operations."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.creative_checker import contracts as C
from backend.creative_checker.models import (
    Approval,
    Asset,
    CriterionSet,
    Document,
    Job,
    Project,
    User,
    new_id,
    utcnow,
)
from backend.creative_checker.services.common import fail, next_version
from backend.creative_checker.services.events import invalidate_assets, record_event
from backend.creative_checker.services.files import store_file, validate_document
from backend.creative_checker.services.serialization import criteria_json


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
