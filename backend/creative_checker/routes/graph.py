"""HTTP routes for graph."""

from fastapi import APIRouter
from sqlalchemy import select

from backend.creative_checker import services as S
from backend.creative_checker.http import Db, Identity
from backend.creative_checker.models import CriterionSet


def create_router(settings):
    router = APIRouter()

    @router.get("/api/projects/{project_id}/graph")
    def graph(project_id: str, db: Db, identity: Identity):
        from backend.creative_checker.graph import GraphStore, GraphUnavailable

        project, _role = S.project_access(db, project_id, identity[0])
        graph_store = GraphStore(settings)
        try:
            result = graph_store.project_graph(project.id)
            result["status"] = project.graph_status
            result["synced_at"] = S.iso(project.graph_synced_at)
            return result
        except GraphUnavailable:
            S.fail(503, "graph_unavailable", "The relationship store is currently unavailable")
        finally:
            graph_store.close()

    @router.get("/api/projects/{project_id}/impact")
    def impact(project_id: str, criterion_id: str, db: Db, identity: Identity):
        from backend.creative_checker.graph import GraphStore, GraphUnavailable

        project, _role = S.project_access(db, project_id, identity[0])
        criterion_id = S.uuid_string(criterion_id)
        # Check ownership in the source of truth before querying a derived index.
        sets = db.scalars(select(CriterionSet).where(CriterionSet.project_id == project.id)).all()
        if not any(item.get("id") == criterion_id for criteria in sets for item in criteria.items):
            S.fail(404, "not_found", "Criterion not found")
        graph_store = GraphStore(settings)
        try:
            return graph_store.impact(project.id, criterion_id)
        except GraphUnavailable:
            S.fail(503, "graph_unavailable", "The relationship store is currently unavailable")
        finally:
            graph_store.close()

    return router
