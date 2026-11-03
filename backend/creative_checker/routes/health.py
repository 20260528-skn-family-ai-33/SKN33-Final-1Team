"""HTTP routes for health."""

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.creative_checker import services as S
from backend.creative_checker.http import Db
from backend.creative_checker.models import (
    Approval,
    Asset,
    AssetVersion,
    AuthSession,
    ChangeRequest,
    CriterionSet,
    Document,
    Event,
    FileRecord,
    Job,
    Logo,
    Membership,
    Outbox,
    Project,
    Run,
    User,
)


def create_router():
    router = APIRouter()

    @router.get("/api/health/live")
    def live():
        return {"status": "ok"}

    @router.get("/api/health/ready")
    def ready(db: Db):
        from backend.creative_checker.tool_records import ToolCall

        try:
            for model in (User, AuthSession, Project, Membership, FileRecord, Document, CriterionSet,
                          Asset, AssetVersion, Logo, Run, ChangeRequest, Approval, Event, Job, Outbox, ToolCall):
                db.execute(select(model.id).limit(0))
        except SQLAlchemyError:
            S.fail(503, "database_not_ready", "The database is unavailable or its schema has not been initialized")
        return {"status": "ready"}

    return router
