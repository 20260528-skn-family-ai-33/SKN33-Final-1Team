"""Product access operations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.creative_checker.models import Membership, Project, User
from backend.creative_checker.services.common import fail, uuid_string


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
