"""Product assets operations."""

from copy import deepcopy

from sqlalchemy.orm import Session

from backend.creative_checker.models import (
    Asset,
    AssetVersion,
    ChangeRequest,
    Project,
    Run,
    User,
    new_id,
)
from backend.creative_checker.services.common import fail, next_version, uuid_string
from backend.creative_checker.services.events import record_event
from backend.creative_checker.services.files import store_file


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
