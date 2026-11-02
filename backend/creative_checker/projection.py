"""Transactional outbox delivery and repeatable reconstruction from PostgreSQL."""
from datetime import timedelta

from sqlalchemy import select, update

from .graph import GraphStore
from .models import Approval, Asset, AssetVersion, ChangeRequest, CriterionSet, Document, FileRecord, Logo, Outbox, Project, Run, utcnow


def project_snapshot(db, project_id):
    nodes, edges = {}, []

    def node(identity, kind, label, **properties):
        nodes[identity] = {"id": identity, "kind": kind, "label": label, **properties}

    def edge(source, target, kind):
        if source and target:
            edges.append({"source": source, "target": target, "kind": kind})

    project = db.get(Project, project_id)
    node(project.id, "project", project.name)
    for document in db.scalars(select(Document).where(Document.project_id == project_id)):
        node(document.id, "document", f"문서 v{document.version}: {document.filename}")
        edge(project_id, document.id, "contains")
    for criteria in db.scalars(select(CriterionSet).where(CriterionSet.project_id == project_id)):
        node(criteria.id, "criteria", f"검수 기준 v{criteria.version}", status=criteria.status)
        edge(criteria.document_id, criteria.id, "defines")
        for item in criteria.items:
            identity = f"{criteria.id}:{item['id']}"
            node(identity, "criterion", item["text"], criterion_id=item["id"], criteria_version_id=criteria.id)
            edge(criteria.id, identity, "contains")
    for asset in db.scalars(select(Asset).where(Asset.project_id == project_id)):
        node(asset.id, "asset", asset.name, asset_kind=asset.kind)
        edge(project_id, asset.id, "contains")
    for version in db.scalars(select(AssetVersion).where(AssetVersion.project_id == project_id)):
        node(version.id, "asset_version", f"산출물 v{version.version}", asset_id=version.asset_id)
        edge(version.asset_id, version.id, "version")
        edge(version.change_request_id, version.id, "resubmitted_as")
    for logo in db.scalars(select(Logo).where(Logo.project_id == project_id)):
        node(logo.id, "logo", f"{logo.name} v{logo.version}", status=logo.status)
        edge(project_id, logo.id, "contains")
    for run in db.scalars(select(Run).where(Run.project_id == project_id)):
        node(run.id, "run", f"검수 {run.id[:8]}", state=run.state, verdict=run.verdict or "pending")
        edge(run.id, run.asset_version_id, "inspects")
        edge(run.id, run.criteria_version_id, "applies")
        edge(run.previous_run_id, run.id, "reinspected_as")
        for logo in run.snapshot.get("logos", []):
            edge(run.id, logo["id"], "compares")
        for result in run.results:
            identity = f"{run.id}:{result['criterion_id']}"
            node(identity, "result", result.get("reason", "검사 결과"), verdict=result.get("verdict", "review"), run_id=run.id)
            edge(run.id, identity, "produces")
            edge(f"{run.criteria_version_id}:{result['criterion_id']}", identity, "evaluated_by")
            for evidence in result.get("evidence", []):
                file_id = evidence["file_id"]
                node(file_id, "evidence", f"근거 {file_id[:8]}", file_id=file_id)
                edge(identity, file_id, "supported_by")
    for change in db.scalars(select(ChangeRequest).where(ChangeRequest.project_id == project_id)):
        node(change.id, "change_request", change.message or "수정 요청", status=change.status)
        edge(change.run_id, change.id, "requests")
    for approval in db.scalars(select(Approval).where(Approval.project_id == project_id)):
        node(approval.id, "approval", "예외 포함 승인" if approval.exception_reason else "승인", target_type=approval.target_type)
        edge(approval.id, approval.target_id, "approves")
    # Results may contain deliberately retained invalid model IDs; they cannot create dangling graph edges.
    return list(nodes.values()), [e for e in edges if e["source"] in nodes and e["target"] in nodes]


def deliver_one(sessions, graph: GraphStore):
    with sessions.begin() as db:
        candidate = db.scalar(select(Outbox).where(Outbox.processed_at.is_(None), Outbox.available_at <= utcnow())
                              .order_by(Outbox.created_at, Outbox.id).limit(1))
        if candidate is None:
            return False
        project_id = candidate.project_id
        # Project lock orders concurrent snapshots and keeps the revision consistent with content.
        project = db.scalar(select(Project).where(Project.id == project_id).with_for_update(skip_locked=True))
        if project is None:
            return False
        item = db.scalar(select(Outbox).where(Outbox.id == candidate.id, Outbox.processed_at.is_(None))
                         .with_for_update(skip_locked=True))
        if item is None:
            return False
        revision = project.graph_revision
        try:
            nodes, edges = project_snapshot(db, project_id)
            graph.replace_project(project_id, nodes, edges, revision)
        except Exception:
            project.graph_status = "error"
            project.graph_error = {"code": "graph_unavailable", "message": "관계 저장소 동기화가 지연되고 있습니다."}
            item.attempt += 1
            item.error = project.graph_error
            delay = min(300, 2 ** min(item.attempt, 8))
            # Defer the whole project, avoiding a retry storm over its other events.
            db.execute(update(Outbox).where(Outbox.project_id == project_id, Outbox.processed_at.is_(None))
                       .values(available_at=utcnow() + timedelta(seconds=delay)))
        else:
            project.graph_status, project.graph_synced_at, project.graph_error = "synced", utcnow(), None
            db.execute(update(Outbox).where(Outbox.project_id == project_id, Outbox.processed_at.is_(None))
                       .values(processed_at=utcnow(), error=None))
        return True
