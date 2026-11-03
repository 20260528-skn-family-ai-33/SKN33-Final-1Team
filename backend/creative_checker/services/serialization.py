"""Product serialization operations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.creative_checker.models import (
    Approval,
    Asset,
    AssetVersion,
    ChangeRequest,
    CriterionSet,
    Document,
    Event,
    Logo,
    Project,
    Run,
)
from backend.creative_checker.services.common import iso


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
