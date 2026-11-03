"""HTTP routes for projects."""

from fastapi import APIRouter
from sqlalchemy import func, select

from backend.creative_checker import contracts as C, services as S, auth
from backend.creative_checker.http import APPROVERS, Db, Identity
from backend.creative_checker.models import Membership, Project, User, new_id


def create_router():
    router = APIRouter()

    @router.get("/api/projects")
    def projects(db: Db, identity: Identity):
        rows = db.execute(select(Project, Membership.role).join(Membership, Membership.project_id == Project.id)
                          .where(Membership.user_id == identity[0].id).order_by(Project.created_at.desc(), Project.id)).all()
        return {"items": [S.project_json(project, role) for project, role in rows]}

    @router.post("/api/projects", status_code=201)
    def add_project(body: C.CreateProject, db: Db, identity: Identity):
        user = identity[0]
        project = Project(id=new_id(), name=body.name, description=body.description, created_by=user.id)
        db.add(project)
        db.flush()
        db.add(Membership(project_id=project.id, user_id=user.id, role="approver"))
        S.record_event(db, project, "project.created", user.id, {"project_id": project.id})
        S.commit(db)
        return S.project_json(project, "approver")

    @router.get("/api/projects/{project_id}")
    def project_detail(project_id: str, db: Db, identity: Identity):
        project, role = S.project_access(db, project_id, identity[0])
        return S.project_json(project, role)

    @router.get("/api/projects/{project_id}/members")
    def members(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        rows = db.execute(select(Membership, User).join(User, Membership.user_id == User.id)
                          .where(Membership.project_id == project.id).order_by(User.name, User.id)).all()
        return {"items": [{"id": membership.id, "user_id": user.id, "role": membership.role, "name": user.name, "email": user.email,
                           "user": auth.public_user(user)} for membership, user in rows]}

    @router.put("/api/projects/{project_id}/members")
    def put_member(project_id: str, body: C.SetMember, db: Db, identity: Identity):
        user = identity[0]
        project, _role = S.project_access(db, project_id, user, APPROVERS, lock=True, writable=True)
        target = db.get(User, str(body.user_id))
        if target is None or not target.active:
            S.fail(404, "not_found", "User not found")
        member = db.scalar(select(Membership).where(Membership.project_id == project.id, Membership.user_id == target.id))
        if member and member.role == "approver" and body.role != "approver":
            count = db.scalar(select(func.count()).select_from(Membership).where(Membership.project_id == project.id, Membership.role == "approver"))
            if count <= 1:
                S.fail(409, "last_approver", "A project must keep at least one approver")
        if member:
            member.role = body.role
        else:
            member = Membership(project_id=project.id, user_id=target.id, role=body.role)
            db.add(member)
        S.record_event(db, project, "membership.changed", user.id, {"user_id": target.id, "role": body.role})
        S.commit(db)
        return {"id": member.id, "user_id": target.id, "role": member.role, "name": target.name, "email": target.email,
                "user": auth.public_user(target)}

    @router.post("/api/projects/{project_id}/archive")
    def archive(project_id: str, body: C.ArchiveProject, db: Db, identity: Identity):
        project, role = S.project_access(db, project_id, identity[0], APPROVERS, lock=True)
        project.archived = body.archived
        S.record_event(db, project, "project.archived" if body.archived else "project.restored", identity[0].id, {"archived": body.archived})
        S.commit(db)
        return S.project_json(project, role)

    return router
