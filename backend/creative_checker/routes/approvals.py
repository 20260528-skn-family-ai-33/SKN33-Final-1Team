"""HTTP routes for approvals."""

from fastapi import APIRouter
from sqlalchemy import select

from backend.creative_checker import contracts as C, services as S
from backend.creative_checker.http import APPROVERS, Db, EDITORS, Identity
from backend.creative_checker.models import Approval, ChangeRequest, Event, Run


def create_router():
    router = APIRouter()

    @router.get("/api/runs/{run_id}/change-requests")
    def change_requests(run_id: str, db: Db, identity: Identity):
        run, _project, _role = S.object_access(db, Run, run_id, identity[0])
        return {"items": [S.change_json(change) for change in db.scalars(select(ChangeRequest).where(ChangeRequest.run_id == run.id).order_by(ChangeRequest.created_at, ChangeRequest.id))]}

    @router.post("/api/runs/{run_id}/change-requests", status_code=201)
    def add_change_request(run_id: str, body: C.CreateChangeRequest, db: Db, identity: Identity):
        run, project, _role = S.object_access(db, Run, run_id, identity[0], EDITORS, lock=True, writable=True)
        change = S.create_change_request(db, project, run, identity[0], body)
        S.commit(db)
        return S.change_json(change)

    @router.post("/api/change-requests/{change_id}/approve")
    def approve_change(change_id: str, db: Db, identity: Identity):
        change, project, _role = S.object_access(db, ChangeRequest, change_id, identity[0], APPROVERS, lock=True, writable=True)
        S.approve_change_request(db, project, change, identity[0])
        S.commit(db)
        return S.change_json(change)

    @router.post("/api/runs/{run_id}/approve", status_code=201)
    def approve_run(run_id: str, body: C.FinalApproval, db: Db, identity: Identity):
        run, project, _role = S.object_access(db, Run, run_id, identity[0], APPROVERS, lock=True, writable=True)
        approval = S.approve_run(db, project, run, identity[0], body)
        S.commit(db)
        return S.approval_json(approval)

    @router.get("/api/projects/{project_id}/history")
    def history(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        events = db.scalars(select(Event).where(Event.project_id == project.id).order_by(Event.created_at.desc(), Event.id)).all()
        approvals = db.scalars(select(Approval).where(Approval.project_id == project.id).order_by(Approval.created_at.desc(), Approval.id)).all()
        return {"items": [S.event_json(event) for event in events], "approvals": [S.approval_json(approval) for approval in approvals]}

    return router
