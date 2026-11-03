"""HTTP routes for runs."""

from typing import Annotated

from fastapi import APIRouter, Header
from sqlalchemy import select

from backend.creative_checker import contracts as C, services as S
from backend.creative_checker.http import Db, EDITORS, Identity
from backend.creative_checker.models import Run


def create_router(settings):
    router = APIRouter()

    @router.get("/api/projects/{project_id}/runs")
    def runs(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        return {"items": [S.run_json(db, run) for run in db.scalars(select(Run).where(Run.project_id == project.id).order_by(Run.created_at.desc(), Run.id))]}

    @router.post("/api/projects/{project_id}/runs", status_code=201)
    def add_run(project_id: str, body: C.CreateRun, db: Db, identity: Identity,
                idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
        project, _role = S.project_access(db, project_id, identity[0], EDITORS, lock=True, writable=True)
        run = S.create_run(db, project, identity[0], body, idempotency_key, settings)
        S.commit(db)
        return S.run_json(db, run)

    @router.get("/api/runs/{run_id}")
    def run_detail(run_id: str, db: Db, identity: Identity):
        run, _project, _role = S.object_access(db, Run, run_id, identity[0])
        return S.run_json(db, run)

    @router.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str, db: Db, identity: Identity):
        run, project, _role = S.object_access(db, Run, run_id, identity[0], EDITORS, lock=True, writable=True)
        S.cancel_run(db, project, run, identity[0])
        S.commit(db)
        return S.run_json(db, run)

    @router.post("/api/runs/{run_id}/retry")
    def retry_run(run_id: str, db: Db, identity: Identity,
                  idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
        run, project, _role = S.object_access(db, Run, run_id, identity[0], EDITORS, lock=True, writable=True)
        S.retry_run(db, project, run, identity[0], idempotency_key)
        S.commit(db)
        return S.run_json(db, run)

    return router
