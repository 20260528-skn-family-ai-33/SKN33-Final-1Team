"""Opt-in PostgreSQL/Neo4j workflows. Qwen replies are explicitly synthetic."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

from langgraph.checkpoint.postgres import PostgresSaver
import pytest
from sqlalchemy import func, select

from backend.creative_checker import worker
from backend.creative_checker.cli import request_graph_rebuild
from backend.creative_checker.models import Approval, Document, Job, Outbox, Project, Run
from backend.creative_checker.orchestration import build_graph
from backend.creative_checker.projection import deliver_one, project_snapshot
from backend.creative_checker.tool_records import ToolCall
from backend.creative_checker.tools import ToolFailure

from integration_helpers import IsolatedServices, checked, image_bytes, integration_config

pytestmark = pytest.mark.integration


@pytest.fixture
def real_services(tmp_path, monkeypatch):
    with IsolatedServices(integration_config(), tmp_path, monkeypatch) as harness:
        yield harness


def test_real_api_document_review_reinspection_approval_and_graph_rebuild(real_services):
    h = real_services
    project, document, criteria = h.approved_document()
    asset = checked(h.http.post(f"/api/projects/{project['id']}/assets", data={"name": "Synthetic poster", "kind": "poster"},
                   files={"file": ("poster.png", image_bytes("red"), "image/png")}), 201)
    original = asset["versions"][0]
    body = {"asset_version_id": original["id"]}
    first = checked(h.http.post(f"/api/projects/{project['id']}/runs", json=body, headers={"Idempotency-Key": "first-review"}), 201)
    replay = checked(h.http.post(f"/api/projects/{project['id']}/runs", json=body, headers={"Idempotency-Key": "first-review"}), 201)
    assert replay["id"] == first["id"]
    review_job = h.step("review", first["id"])
    first = checked(h.http.get(f"/api/runs/{first['id']}"))
    assert first["state"] == "waiting_review" and first["verdict"] == "fail"
    assert first["snapshot"]["criteria"]["id"] == criteria["id"]
    assert h.checkpoint(review_job).next == ("review_gate",)
    evidence_id = first["results"][0]["evidence"][0]["file_id"]
    evidence = h.http.get(f"/api/files/{evidence_id}")
    assert evidence.status_code == 200 and evidence.headers["content-type"] == "image/jpeg"

    change = checked(h.http.post(f"/api/runs/{first['id']}/change-requests", json={
        "message": "Synthetic scenario: correct the title",
        "items": [{"criterion_id": criteria["items"][0]["id"], "message": "Use the approved title"}],
    }), 201)
    checked(h.http.post(f"/api/change-requests/{change['id']}/approve"))
    h.step("approval", first["id"])
    assert h.checkpoint(review_job).next == ()
    assert checked(h.http.get(f"/api/runs/{first['id']}"))["state"] == "completed"

    revision = checked(h.http.post(f"/api/assets/{asset['id']}/versions", data={"change_request_id": change["id"]},
                       files={"file": ("revised.png", image_bytes("green"), "image/png")}), 201)
    assert revision["version"] == 2 and revision["file_id"] != original["file_id"]
    second = checked(h.http.post(f"/api/projects/{project['id']}/runs", json={"asset_version_id": revision["id"]},
                                headers={"Idempotency-Key": "full-reinspection"}), 201)
    assert second["previous_run_id"] == first["id"]
    assert second["snapshot"]["items"] == first["snapshot"]["items"]
    second_job = h.step("review", second["id"])
    second = checked(h.http.get(f"/api/runs/{second['id']}"))
    assert second["state"] == "waiting_review" and second["verdict"] == "pass"
    approval = checked(h.http.post(f"/api/runs/{second['id']}/approve", json={}), 201)
    h.step("approval", second["id"])
    assert h.checkpoint(second_job).next == ()
    second = checked(h.http.get(f"/api/runs/{second['id']}"))
    assert second["state"] == "completed" and second["verdict"] == "pass"
    assert second["approval"]["id"] == approval["id"]
    assert h.http.get(f"/api/files/{original['file_id']}").content == image_bytes("red")
    with h.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "review")) == 2
        receipts = db.scalars(select(ToolCall)).all()
        assert receipts and all(row.state == "completed" for row in receipts)
        assert db.get(Approval, approval["id"]).snapshot["input"]["asset_version"]["id"] == revision["id"]

    # Real graph projection, stale-revision rejection and rebuilding only our UUID.
    assert deliver_one(h.sessions, h.graph)
    assert not deliver_one(h.sessions, h.graph)
    graph = checked(h.http.get(f"/api/projects/{project['id']}/graph"))
    assert graph["status"] == "synced"
    actual_nodes = {node["id"] for node in graph["nodes"]}
    assert {project["id"], document["id"], criteria["id"], first["id"], second["id"], approval["id"]} <= actual_nodes
    with h.sessions() as db:
        nodes, edges = project_snapshot(db, project["id"])
        revision_number = db.get(Project, project["id"]).graph_revision
    expected_edges = {(edge["source"], edge["target"], edge["kind"]) for edge in edges}
    assert actual_nodes == {node["id"] for node in nodes}
    assert {(edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]} == expected_edges
    assert h.graph.replace_project(project["id"], [], [], revision_number - 1) is False
    impact = checked(h.http.get(f"/api/projects/{project['id']}/impact", params={"criterion_id": criteria["items"][0]["id"]}))
    assert {item["run_id"] for item in impact["items"]} == {first["id"], second["id"]}
    assert approval["id"] in next(item["approval_ids"] for item in impact["items"] if item["run_id"] == second["id"])
    h.graph.delete_project(project["id"])
    assert h.graph.project_graph(project["id"])["status"] == "pending"
    with h.sessions.begin() as db:
        assert request_graph_rebuild(db, project["id"]) == 1
    assert deliver_one(h.sessions, h.graph)
    restored = h.graph.project_graph(project["id"])
    assert {node["id"] for node in restored["nodes"]} == actual_nodes
    assert {(edge["source"], edge["target"], edge["kind"]) for edge in restored["edges"]} == expected_edges
    with h.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.processed_at.is_(None))) == 0
    assert h.synthetic_calls == ["extract_criteria", "inspect", "report", "inspect", "report"]


def test_postgres_claim_is_exclusive_and_old_lease_cannot_finish(real_services):
    h = real_services
    project = h.project()
    document = h.document(project["id"])
    barrier = Barrier(4)

    def claim_together():
        barrier.wait(timeout=10)
        return worker.claim(h.sessions)

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: claim_together(), range(4)))
    winners = [job for job in claims if job is not None]
    assert len(winners) == 1
    original = winners[0]
    assert original.target_id == document["id"]
    assert worker.claim(h.sessions) is None
    h.expire(original.id)
    replacement = worker.claim(h.sessions)
    assert replacement.id == original.id and replacement.lease_token != original.lease_token
    worker.finish(h.sessions, original, error={"code": "stale_worker", "message": "Must not persist"})
    with h.sessions() as db:
        current = db.get(Job, replacement.id)
        assert current.state == "running" and current.lease_token == replacement.lease_token and current.error is None
    h.process(replacement)
    with h.sessions() as db:
        assert db.get(Job, replacement.id).state == "completed"
        assert db.get(Document, document["id"]).status == "waiting_review"


@pytest.mark.parametrize("kind", ["document", "review"])
def test_restart_after_human_checkpoint_restores_approvable_state(real_services, monkeypatch, kind):
    h = real_services
    if kind == "document":
        project = h.project()
        target_id = h.document(project["id"])["id"]
    else:
        project, _document, _criteria = h.approved_document()
        asset = checked(h.http.post(f"/api/projects/{project['id']}/assets", data={"name": "Poster", "kind": "poster"},
                       files={"file": ("poster.png", image_bytes("green"), "image/png")}), 201)
        target_id = checked(h.http.post(f"/api/projects/{project['id']}/runs", json={"asset_version_id": asset["versions"][0]["id"]},
                                       headers={"Idempotency-Key": "crash-at-human-gate"}), 201)["id"]
    job = worker.claim(h.sessions)
    assert job is not None and job.kind == kind and job.target_id == target_id

    class SimulatedProcessExit(BaseException):
        pass

    # Checkpoint is committed; cut before Job completion is committed.
    with monkeypatch.context() as cut:
        cut.setattr(worker, "finish", lambda *args, **kwargs: (_ for _ in ()).throw(SimulatedProcessExit()))
        with pytest.raises(SimulatedProcessExit):
            h.process(job)
    assert h.checkpoint(job).next == (("criteria_gate",) if kind == "document" else ("review_gate",))
    model_calls = list(h.synthetic_calls)
    h.expire(job.id)
    replacement = worker.claim(h.sessions)
    assert replacement.id == job.id and replacement.lease_token != job.lease_token
    h.process(replacement, fresh_engine=True)
    assert h.synthetic_calls == model_calls, "Restart must not repeat inference before a persisted human gate"
    with h.sessions() as db:
        assert db.get(Job, job.id).state == "completed"
        target = db.get(Document if kind == "document" else Run, target_id)
        actual_state = target.status if kind == "document" else target.state
        assert actual_state == "waiting_review", "Reclaim must restore the human gate's state instead of leaving it running"
    if kind == "document":
        document = next(item for item in checked(h.http.get(f"/api/projects/{project['id']}/documents"))["items"] if item["id"] == target_id)
        criteria = checked(h.http.post(f"/api/projects/{project['id']}/criteria", json={
            "document_id": target_id, "items": document["criteria"], "groups": document["groups"],
        }), 201)
        checked(h.http.post(f"/api/criteria/{criteria['id']}/approve"))
    else:
        checked(h.http.post(f"/api/runs/{target_id}/approve", json={}), 201)
    h.step("approval", target_id)
    assert h.checkpoint(job).next == ()


def test_reclaimed_lease_stops_old_graph_before_tool_side_effects(real_services):
    h = real_services
    project = h.project()
    document = h.document(project["id"])
    old = worker.claim(h.sessions)
    h.expire(old.id)
    replacement = worker.claim(h.sessions)
    assert replacement.lease_token != old.lease_token
    with PostgresSaver.from_conn_string(worker.checkpoint_dsn(h.settings.database_url)) as saver:
        graph = build_graph(h.settings, h.sessions, saver, lease_token=old.lease_token)
        with pytest.raises(ToolFailure) as error:
            graph.invoke({"job_id": old.id, "kind": "document", "target_id": document["id"], "attempt": old.attempt},
                         {"configurable": {"thread_id": worker.thread_id(old) + ":stale-executor"}})
        assert error.value.code == "cancelled"
    with h.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ToolCall).where(ToolCall.job_id == old.id)) == 0, \
            "A stale lease must stop before creating a tool receipt or touching input files"
        assert db.get(Job, old.id).lease_token == replacement.lease_token


def test_real_mcp_boundary_rejects_stale_and_missing_lease_before_receipts(real_services):
    h = real_services
    assert h.settings.testing is False
    project = h.project()
    h.document(project["id"])
    old = worker.claim(h.sessions)
    h.expire(old.id)
    current = worker.claim(h.sessions)
    assert current.lease_token != old.lease_token

    with h.mcp_client() as (client, headers):
        request_id = 1

        def call(name, arguments):
            nonlocal request_id
            request_id += 1
            response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": request_id,
                "method": "tools/call", "params": {"name": name, "arguments": arguments}})
            assert response.status_code == 200, response.text
            envelope = response.json()["result"]
            assert not envelope.get("isError"), envelope
            return envelope.get("structuredContent") or json.loads(envelope["content"][0]["text"])

        assert client.post("/mcp", json={}).status_code == 401
        for token in (None, old.lease_token):
            args = {"job_id": old.id, **({"lease_token": token} if token else {})}
            for name in ("read_document", "prepare_media", "submit_inference"):
                extra = {"operation": "extract_criteria"} if name == "submit_inference" else {}
                denied = call(name, {**args, **extra})
                assert denied["state"] == "failed" and denied["error"]["code"] == "cancelled"
        with h.sessions() as db:
            assert db.scalar(select(func.count()).select_from(ToolCall).where(ToolCall.job_id == old.id)) == 0
        accepted = call("read_document", {"job_id": current.id, "lease_token": current.lease_token})
        assert accepted["state"] == "completed"
        assert any("Expected title" in block["text"] for block in accepted["result"]["blocks"])
        # Authorization must run before returning even an existing cached receipt.
        denied_cached = call("read_document", {"job_id": old.id, "lease_token": old.lease_token})
        assert denied_cached["state"] == "failed" and denied_cached["error"]["code"] == "cancelled"
        repeated = call("read_document", {"job_id": current.id, "lease_token": current.lease_token})
        assert repeated["call_id"] == accepted["call_id"]
    with h.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ToolCall).where(ToolCall.job_id == old.id)) == 1
        assert db.get(Job, old.id).lease_token == current.lease_token
    assert h.synthetic_calls == [], "The boundary test must not invoke a model"
