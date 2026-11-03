"""HTTP routes for documents."""

from fastapi import APIRouter, File, UploadFile
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from backend.creative_checker import contracts as C, services as S
from backend.creative_checker.http import APPROVERS, Db, EDITORS, Identity, upload_bytes
from backend.creative_checker.models import CriterionSet, Document


def create_router(settings, storage):
    router = APIRouter()

    @router.get("/api/projects/{project_id}/documents")
    def documents(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        return {"items": [S.document_json(document) for document in db.scalars(select(Document).where(Document.project_id == project.id).order_by(Document.version.desc()))]}

    @router.post("/api/projects/{project_id}/documents", status_code=201)
    async def add_document(project_id: str, db: Db, identity: Identity, file: UploadFile = File(...)):
        project, _role = S.project_access(db, project_id, identity[0], EDITORS, lock=True, writable=True)
        filename, data = await upload_bytes(file, 20 * 1024 * 1024)
        document = await run_in_threadpool(S.register_document, db, storage, project, identity[0], filename, data, settings)
        S.commit(db)
        return S.document_json(document)

    @router.get("/api/projects/{project_id}/criteria")
    def criteria(project_id: str, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0])
        return {"items": [S.criteria_json(c) for c in db.scalars(select(CriterionSet).where(CriterionSet.project_id == project.id).order_by(CriterionSet.version.desc()))]}

    @router.post("/api/projects/{project_id}/criteria", status_code=201)
    def add_criteria(project_id: str, body: C.CreateCriteria, db: Db, identity: Identity):
        project, _role = S.project_access(db, project_id, identity[0], EDITORS, lock=True, writable=True)
        criteria = S.create_criteria(db, project, identity[0], body)
        S.commit(db)
        return S.criteria_json(criteria)

    @router.post("/api/criteria/{criteria_id}/approve")
    def approve_criteria(criteria_id: str, db: Db, identity: Identity):
        criteria, project, _role = S.object_access(db, CriterionSet, criteria_id, identity[0], APPROVERS, lock=True, writable=True)
        S.approve_criteria(db, project, criteria, identity[0])
        S.commit(db)
        return S.criteria_json(criteria)

    return router
