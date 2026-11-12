"""Video approval history on the disposable PostgreSQL and Neo4j servers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, text

from backend.agent_framework.graph_projection import ProjectionWorker
from backend.agent_framework.graph_store import FrameworkGraph
from backend.agent_framework.postgres_schema import migrate
from backend.app.persistence.schema.identity import project_members
from backend.app.persistence.transaction import unit_of_work
from backend.app.video import review as video_review
from backend.app.video.framework_service import create_video_app
from backend.app.video.workflow import VideoDecision, prepare_review, record_decision
from tests.agent_framework.graph_test_environment import isolated_graph_settings
from tests.agent_framework.test_pdf_postgres_integration import document_database, runtime  # noqa: F401
from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database, csrf, email_user, login
from tests.app.test_video_review_api import SUBTITLE, criterion, fake_ocr, make_video

pytestmark = pytest.mark.db_integration
API = "/api/video-reviews"


def test_video_approval_import_revisions_permissions_and_graph(runtime, tmp_path, monkeypatch):
    graph_settings = isolated_graph_settings()
    r = runtime
    assert migrate(r.migration, target="AF005")["revision"] == "AF005"
    monkeypatch.setenv("VIDEO_SUBTITLE_ENGINE", "paddleocr")
    calls = []
    monkeypatch.setattr(video_review, "subtitle_frames", fake_ocr(calls))
    monkeypatch.setattr(video_review, "logo_readiness_default", lambda: (False, "unused"))
    data = make_video(tmp_path / "clip.mp4", seconds=2)
    video_root = tmp_path / "video"
    files = {"video": ("clip.mp4", data, "video/mp4")}
    media_form = {"criteria": json.dumps([criterion(f'"{SUBTITLE}"')]), "disclosure": '{"kind":"self"}'}
    form = media_form | {"project_id": str(r.pid), "criteria_confirmed": "true"}

    @contextmanager
    def video_client():
        app = create_video_app(r.settings, video_root, engine=r.db.engine, readiness=lambda: (True, "ready"))
        with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 1234)) as client:
            client.headers["Origin"] = "http://localhost:8000"
            client.cookies.update(r.client.cookies)
            yield client

    with video_client() as client:
        assert client.get(API + "/status").json()["project_reviews"] is False
        unavailable = client.post(API, data=form, files=files, headers=csrf(client))
        assert unavailable.status_code == 503, unavailable.text
        first = client.post(API, data=media_form, files=files, headers=csrf(client))
        assert first.status_code == 202, first.text
        parent_id = first.json()["id"]
        parent_path = API + "/" + parent_id
        legacy = client.get(parent_path).json()
        assert legacy["status"] == "completed"

    legacy["criteria_version"], legacy["workflow"] = prepare_review(
        parent_id, str(r.pid), str(r.subject.user_id), legacy["criteria"], legacy["disclosure_input"],
        None, legacy["created_at"],
    )
    change = VideoDecision(command_id=uuid4(), decision="approve_changes", reason="표현 조정",
                           criteria_version=1, asset_sha256=legacy["media"]["sha256"])
    record_decision(legacy, change, str(r.subject.user_id), legacy["updated_at"])
    legacy_path = video_root / "reviews" / parent_id / "result.json"
    legacy_bytes = json.dumps(legacy).encode()
    legacy_path.write_bytes(legacy_bytes)
    assert migrate(r.migration, target="AF006")["revision"] == "AF006"

    with video_client() as client:
        assert client.get(API + "/status").json()["project_reviews"] is True
        assert client.get(parent_path).json()["workflow"]["decisions"] == legacy["workflow"]["decisions"]
        request_headers = csrf(client) | {"Idempotency-Key": str(uuid4())}
        child_form = form | {"parent_run_id": parent_id}
        child = client.post(API, data=child_form, files=files, headers=request_headers)
        assert child.status_code == 202, child.text
        child_id = child.json()["id"]
        child_path = API + "/" + child_id
        replay = client.post(API, data=child_form, files=files, headers=request_headers)
        assert replay.status_code == 202 and replay.json() == {"id": child_id, "status": "completed"}
        changed = {"video": ("clip.mp4", data[:-1] + bytes([data[-1] ^ 1]), "video/mp4")}
        assert client.post(API, data=child_form, files=changed, headers=request_headers).status_code == 409
        assert client.post(API, data=child_form, files=files, headers=csrf(client)).status_code == 409
        assert client.post(API, data=form | {"parent_run_id": child_id}, files=files, headers=csrf(client)).status_code == 409
        assert len(calls) == 2

        job = client.get(child_path).json()
        assert job["workflow"]["asset_version"] == 2 and job["workflow"]["decisions"] == []
        final = {"command_id": str(uuid4()), "decision": "approve_final", "criteria_version": job["criteria_version"],
                 "asset_sha256": job["media"]["sha256"]}
        approvals = child_path + "/approvals"
        assert client.post(approvals, json=final | {"criteria_version": 9}, headers=csrf(client)).status_code == 409
        viewer = email_user(r.auth_database)
        client.cookies.clear()
        assert login(client, email=viewer["email"]).status_code == 200
        assert client.get(child_path).status_code == 404
        with r.owner.begin() as c:
            c.execute(insert(project_members).values(project_id=r.pid, user_id=UUID(viewer["id"]), role="viewer"))
        assert client.get(child_path).status_code == 200
        assert client.post(approvals, json=final, headers=csrf(client)).status_code == 403
        client.cookies.clear()
        client.cookies.update(r.client.cookies)
        headers = csrf(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(client.post, approvals, json=final, headers=headers) for _ in range(2)]
            saved = [call.result() for call in pending]
        assert [result.status_code for result in saved] == [200, 200], [result.text for result in saved]
        decisions = saved[0].json()["workflow"]["decisions"]
        assert len(decisions) == 1 and decisions == saved[1].json()["workflow"]["decisions"]
        assert client.post(approvals, json=final | {"reason": "다른 요청"}, headers=headers).status_code == 409

    child_raw = json.loads((video_root / "reviews" / child_id / "result.json").read_text(encoding="utf-8"))
    assert child_raw["workflow"]["decisions"] == [] and legacy_path.read_bytes() == legacy_bytes
    with video_client() as client:
        history = client.get(parent_path + "/revisions").json()["items"]
        assert [(item["asset_version"], item["human_decision"]) for item in history] == [
            (1, "changes_approved"), (2, "final_approved"),
        ]
        assert client.get(child_path).json()["workflow"]["decisions"] == decisions

    with unit_of_work(r.db.engine) as c:
        stored = c.execute(text("SELECT run_id, actor_id, decision FROM agent_framework.video_approvals "
                                "WHERE project_id=:p ORDER BY asset_version"), {"p": r.pid}).all()
        assert stored == [(UUID(parent_id), r.subject.user_id, "approve_changes"),
                          (UUID(child_id), r.subject.user_id, "approve_final")]
        assert c.execute(text("SELECT revision FROM agent_framework.graph_state WHERE project_id=:p"),
                         {"p": r.pid}).scalar_one() == 2
        assert c.exec_driver_sql("SELECT has_table_privilege(current_user, 'agent_framework.video_approvals',"
                                 "'UPDATE,DELETE')").scalar_one() is False

    graph = FrameworkGraph(graph_settings["uri"], "neo4j", graph_settings["password"])
    try:
        graph.initialize()
        worker = ProjectionWorker(r.db.engine, graph)
        assert worker.deliver_one(project_id=r.pid) and worker.last_outcome == "synced"
        assert not worker.deliver_one(project_id=r.pid)
        projected = graph.read_projection(str(r.pid))
        assert projected["revision"] == 2
        nodes = {node["key"]: node for node in projected["nodes"]}
        assert nodes["video:" + child_id]["asset_sha256"] == job["media"]["sha256"]
        assert nodes["video-approval:" + child_id]["decision"] == "approve_final"
        assert nodes["video-approval:" + parent_id]["decision"] == "approve_changes"
        assert {"source": "video:" + parent_id, "target": "video:" + child_id, "kind": "reinspected_as"} in projected["edges"]
        assert {"source": "video-approval:" + child_id, "target": "video:" + child_id, "kind": "approves"} in projected["edges"]
    finally:
        graph.close()
