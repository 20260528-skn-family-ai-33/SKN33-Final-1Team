"""Product common operations."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session


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


def next_version(db: Session, model, parent_column, parent_id: str) -> int:
    return (db.scalar(select(func.max(model.version)).where(parent_column == parent_id)) or 0) + 1
