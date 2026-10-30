"""PostgreSQL source of truth shared by HTTP API and the separate worker."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, event, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


json_type = JSON().with_variant(JSONB(), "postgresql")


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class Created:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Updated:
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Identity, Created, Base):
    __tablename__ = "cc_users"
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Identity, Created, Base):
    __tablename__ = "cc_auth_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("cc_users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(Identity, Created, Updated, Base):
    __tablename__ = "cc_projects"
    __table_args__ = (CheckConstraint("graph_status in ('pending','synced','error')"),)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("cc_users.id"))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    current_document_id: Mapped[str | None] = mapped_column(ForeignKey("cc_documents.id", use_alter=True, name="fk_cc_current_document"))
    current_criteria_id: Mapped[str | None] = mapped_column(ForeignKey("cc_criterion_sets.id", use_alter=True, name="fk_cc_current_criteria"))
    graph_status: Mapped[str] = mapped_column(String(30), default="pending")
    graph_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    graph_error: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    graph_revision: Mapped[int] = mapped_column(Integer, default=0)


class Membership(Identity, Created, Base):
    __tablename__ = "cc_memberships"
    __table_args__ = (UniqueConstraint("project_id", "user_id"), CheckConstraint("role in ('approver','editor','viewer')"))
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("cc_users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))


class FileRecord(Identity, Created, Base):
    __tablename__ = "cc_files"
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(600), unique=True)
    media_type: Mapped[str] = mapped_column(String(150))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))


class Document(Identity, Created, Updated, Base):
    __tablename__ = "cc_documents"
    __table_args__ = (UniqueConstraint("project_id", "version"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    file_id: Mapped[str] = mapped_column(ForeignKey("cc_files.id"))
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    blocks: Mapped[list] = mapped_column(json_type, default=list)
    criteria: Mapped[list] = mapped_column(json_type, default=list)
    groups: Mapped[list] = mapped_column(json_type, default=list)
    errors: Mapped[list] = mapped_column(json_type, default=list)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))


class CriterionSet(Identity, Created, Base):
    __tablename__ = "cc_criterion_sets"
    __table_args__ = (UniqueConstraint("project_id", "version"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("cc_documents.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    items: Mapped[list] = mapped_column(json_type, default=list)
    groups: Mapped[list] = mapped_column(json_type, default=list)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Asset(Identity, Created, Updated, Base):
    __tablename__ = "cc_assets"
    __table_args__ = (CheckConstraint("kind in ('poster','video')"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))


class AssetVersion(Identity, Created, Base):
    __tablename__ = "cc_asset_versions"
    __table_args__ = (UniqueConstraint("asset_id", "version"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("cc_assets.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    file_id: Mapped[str] = mapped_column(ForeignKey("cc_files.id"))
    filename: Mapped[str] = mapped_column(String(255))
    media_metadata: Mapped[dict] = mapped_column("metadata", json_type, default=dict)
    change_request_id: Mapped[str | None] = mapped_column(ForeignKey("cc_change_requests.id", use_alter=True, name="fk_cc_version_change_request"))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))


class Logo(Identity, Created, Base):
    __tablename__ = "cc_logos"
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    file_id: Mapped[str] = mapped_column(ForeignKey("cc_files.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Identity, Created, Updated, Base):
    __tablename__ = "cc_runs"
    __table_args__ = (CheckConstraint("state in ('queued','running','waiting_review','completed','failed','cancel_requested','cancelled')"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    asset_version_id: Mapped[str] = mapped_column(ForeignKey("cc_asset_versions.id"), index=True)
    criteria_version_id: Mapped[str] = mapped_column(ForeignKey("cc_criterion_sets.id"))
    previous_run_id: Mapped[str | None] = mapped_column(ForeignKey("cc_runs.id"))
    state: Mapped[str] = mapped_column(String(30), default="queued")
    stage: Mapped[str] = mapped_column(String(100), default="queued")
    verdict: Mapped[str | None] = mapped_column(String(20))
    progress: Mapped[dict] = mapped_column(json_type, default=lambda: {"completed": 0, "total": 0})
    results: Mapped[list] = mapped_column(json_type, default=list)
    report: Mapped[dict | None] = mapped_column(json_type, default=None, nullable=True)
    errors: Mapped[list] = mapped_column(json_type, default=list)
    snapshot: Mapped[dict] = mapped_column(json_type, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))


class ChangeRequest(Identity, Created, Base):
    __tablename__ = "cc_change_requests"
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("cc_runs.id"), index=True)
    items: Mapped[list] = mapped_column(json_type, default=list)
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Approval(Identity, Created, Base):
    __tablename__ = "cc_approvals"
    __table_args__ = (UniqueConstraint("run_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("cc_runs.id"))
    target_type: Mapped[str] = mapped_column(String(30), default="run")
    target_id: Mapped[str] = mapped_column(String(36))
    approved_by: Mapped[str] = mapped_column(ForeignKey("cc_users.id"))
    exception_reason: Mapped[str | None] = mapped_column(Text)
    exception_criterion_ids: Mapped[list] = mapped_column(json_type, default=list)
    original_verdict: Mapped[str | None] = mapped_column(String(20))
    snapshot: Mapped[dict] = mapped_column(json_type, default=dict)


class Event(Identity, Created, Base):
    __tablename__ = "cc_events"
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("cc_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(100))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("cc_users.id"))
    payload: Mapped[dict] = mapped_column(json_type, default=dict)


class Job(Identity, Created, Updated, Base):
    __tablename__ = "cc_jobs"
    __table_args__ = (
        CheckConstraint("kind in ('document','review','approval')"),
        CheckConstraint("state in ('queued','running','completed','failed','cancel_requested','cancelled')"),
        UniqueConstraint("project_id", "idempotency_key"),
        Index("ix_cc_jobs_claim", "state", "available_at", "lease_until"),
    )
    kind: Mapped[str] = mapped_column(String(20))
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(30), default="queued")
    payload: Mapped[dict] = mapped_column(json_type, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    lease_token: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    request_hash: Mapped[str | None] = mapped_column(String(64))


class Outbox(Identity, Created, Base):
    __tablename__ = "cc_outbox"
    __table_args__ = (Index("ix_cc_outbox_delivery", "processed_at", "available_at"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("cc_projects.id"), index=True)
    payload: Mapped[dict] = mapped_column(json_type, default=dict)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[dict | None] = mapped_column(json_type, nullable=True)


def _immutable_columns(target, columns):
    state = inspect(target)
    if any(state.attrs[name].history.has_changes() for name in columns):
        raise ValueError("Version content is immutable; create a new version")


@event.listens_for(AssetVersion, "before_update")
@event.listens_for(FileRecord, "before_update")
@event.listens_for(Approval, "before_update")
def _immutable_record(_mapper, _connection, target):
    _immutable_columns(target, [column.key for column in inspect(target).mapper.column_attrs])


@event.listens_for(CriterionSet, "before_update")
def _immutable_criteria(_mapper, _connection, target):
    _immutable_columns(target, ("project_id", "document_id", "version", "items", "groups", "created_by", "created_at"))
    state = inspect(target)
    history = state.attrs.approved_at.history
    was_approved = bool(history.deleted and history.deleted[0] is not None) or bool(target.approved_at and not history.has_changes())
    if was_approved and any(state.attrs[name].history.has_changes() for name in ("status", "approved_by", "approved_at")):
        raise ValueError("An approved criterion set is immutable")


@event.listens_for(Logo, "before_update")
def _immutable_logo(_mapper, _connection, target):
    _immutable_columns(target, ("project_id", "name", "file_id", "version", "created_by", "created_at"))
    state = inspect(target)
    history = state.attrs.approved_at.history
    was_approved = bool(history.deleted and history.deleted[0] is not None) or bool(target.approved_at and not history.has_changes())
    if was_approved and any(state.attrs[name].history.has_changes() for name in ("status", "approved_by", "approved_at")):
        raise ValueError("An approved logo is immutable")


@event.listens_for(ChangeRequest, "before_update")
def _immutable_change(_mapper, _connection, target):
    _immutable_columns(target, ("project_id", "run_id", "items", "message", "created_by", "created_at"))


@event.listens_for(Run, "before_update")
def _immutable_run_input(_mapper, _connection, target):
    _immutable_columns(target, ("project_id", "asset_version_id", "criteria_version_id", "previous_run_id", "snapshot", "created_by"))
