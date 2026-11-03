"""Product events operations."""

from copy import deepcopy

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.creative_checker.models import Asset, Event, Outbox, Project, new_id, utcnow
from backend.creative_checker.services.serialization import event_json


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
