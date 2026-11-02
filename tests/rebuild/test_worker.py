"""Local contract checks use synthetic MCP replies, never a paid model."""
from datetime import timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from backend.creative_checker.database import Base, create_engine, session_factory
from backend.creative_checker.models import Asset, AssetVersion, CriterionSet, Document, FileRecord, Job, Project, Run, User, new_id, utcnow
from backend.creative_checker.orchestration import build_graph
from backend.creative_checker.settings import Settings
from backend.creative_checker.tools import ToolService, ToolFailure
from backend.creative_checker.worker import claim, finish, process


@pytest.fixture
def workflow(tmp_path):
    settings = Settings(database_url="sqlite://", testing=True, data_dir=tmp_path)
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)
    with sessions.begin() as db:
        user = User(id=new_id(), email="worker@example.test", name="test", password_hash="unused")
        db.add(user)
        db.flush()
        project = Project(id=new_id(), name="test", created_by=user.id)
        db.add(project)
        db.flush()
        file = FileRecord(id=new_id(), project_id=project.id, storage_key=f"{project.id}/input.txt",
                          filename="input.pdf", size=1, sha256="test", media_type="application/pdf")
        db.add(file)
        db.flush()
        document = Document(id=new_id(), project_id=project.id, version=1, file_id=file.id, filename="input.pdf")
        db.add(document)
        db.flush()
        job = Job(id=new_id(), kind="document", project_id=project.id, target_id=document.id, state="running", lease_token="lease")
        db.add(job)
    yield settings, engine, sessions, project, document, job
    engine.dispose()


def test_document_human_interrupt_survives_resume(workflow):
    settings, _, sessions, _, document, job = workflow
    calls = []

    def tool(name, args):
        calls.append(name)
        result = {"blocks": [{"id": "p1", "page": 1, "text": "행사명"}]} if name == "read_document" else {"items": [], "groups": []}
        return {"state": "completed", "result": result}

    graph = build_graph(settings, sessions, InMemorySaver(), tool)
    config = {"configurable": {"thread_id": "test-document"}}
    first = graph.invoke({"job_id": job.id, "target_id": document.id, "kind": "document", "attempt": 1}, config)
    assert first["__interrupt__"][0].value["kind"] == "criteria_approval"
    with sessions() as db:
        assert db.get(Document, document.id).status == "waiting_review"
    graph.invoke(Command(resume={"decision": "criteria_approved"}), config)
    with sessions() as db:
        assert db.get(Document, document.id).status == "completed"
    assert calls == ["read_document", "submit_inference"]


def test_video_yields_between_batches_and_cannot_pass_unobserved_intervals(workflow):
    settings, _, sessions, project, document, job = workflow
    criterion_id = new_id()
    criterion = {"id": criterion_id, "text": "행사명", "required": True, "category": "creative", "kind": "exact_text",
                 "supported": True, "expected": "행사명", "target": "all", "asset_ids": []}
    with sessions.begin() as db:
        criteria = CriterionSet(id=new_id(), project_id=project.id, document_id=document.id, version=1, items=[criterion])
        asset = Asset(id=new_id(), project_id=project.id, name="video", kind="video")
        db.add_all([criteria, asset])
        db.flush()
        version = AssetVersion(id=new_id(), project_id=project.id, asset_id=asset.id, version=1,
                               file_id=document.file_id, filename="video.mp4")
        db.add(version)
        db.flush()
        run = Run(id=new_id(), project_id=project.id, asset_version_id=version.id, criteria_version_id=criteria.id,
                  snapshot={"items": [criterion], "groups": [], "logos": [], "asset": {"id": asset.id, "kind": "video"}}, state="running")
        db.add(run)
        db.flush()
        current = db.get(Job, job.id)
        current.kind, current.target_id = "review", run.id
    frames = [{"file_id": new_id(), "frame_ms": i * 1000} for i in range(8)]
    batches = []

    def tool(name, args):
        if name == "prepare_media":
            data = {"frames": frames, "coverage": {"kind": "video", "complete": False}, "warnings": ["sampled"]}
        elif name == "read_evidence":
            return next(frame for frame in frames if frame["file_id"] == args["file_id"])
        elif args["operation"] == "inspect":
            batches.append(args["batch_index"])
            frame = frames[args["batch_index"] * 4]
            data = {"results": [{"criterion_id": criterion_id, "execution_status": "completed", "verdict": "pass",
                "observed": "행사명", "expected": "행사명", "reason": "관측됨", "limitations": [],
                "evidence": [{**frame, "bbox": [0.0, 0.0, 1.0, 1.0], "text": "행사명"}]}]}
        else:
            data = {"summary": "표본 검사", "change_request_draft": {"items": [], "message": ""}}
        return {"state": "completed", "result": data}

    graph = build_graph(settings, sessions, InMemorySaver(), tool)
    config = {"configurable": {"thread_id": "test-video"}}
    state = graph.invoke({"job_id": job.id, "target_id": run.id, "kind": "review", "attempt": 1}, config)
    assert state["__interrupt__"][0].value["kind"] == "queue_yield"
    assert batches == [0]
    state = graph.invoke(Command(resume={"decision": "continue"}), config)
    assert state["__interrupt__"][0].value["kind"] == "review_approval"
    assert batches == [0, 1]
    with sessions() as db:
        saved = db.get(Run, run.id)
        assert saved.verdict == "review"
        assert saved.state == "waiting_review"
        assert saved.progress == {"completed": 2, "total": 2, "unit": "batch"}
    graph.invoke(Command(resume={"decision": "final_approved"}), config)
    with sessions() as db:
        assert db.get(Run, run.id).verdict == "review"
        assert db.get(Run, run.id).state == "completed"


def test_failed_run_exception_resumes_without_model_call(api):
    # Reuse HTTP fixtures so the exception is a real authorized command.
    from test_api import ready_run, finish as finish_run
    from sqlalchemy import select

    context = ready_run(api)
    finish_run(api, context.run, state="failed", verdict=None, missing=True)
    response = api.owner.post(f"/api/runs/{context.run['id']}/approve", json={
        "exception_reason": "미검사 조건을 직접 확인함", "exception_criterion_ids": [context.criteria["items"][0]["id"]]})
    assert response.status_code == 201
    with api.sessions.begin() as db:
        job = db.scalar(select(Job).where(Job.kind == "approval", Job.target_id == context.run["id"]))
        job.state, job.lease_token = "running", "approval-lease"
    process(api.settings, api.sessions, InMemorySaver(), job)
    with api.sessions() as db:
        assert db.get(Job, job.id).state == "completed"
        run = db.get(Run, context.run["id"])
        assert run.state == "failed" and run.verdict is None


def test_large_logo_is_preserved_and_derived_for_model(api):
    import io
    from PIL import Image
    from test_api import ready_run
    from backend.creative_checker.models import Logo
    from backend.creative_checker.services import approved_logos

    context = ready_run(api)
    content = io.BytesIO()
    Image.new("RGB", (3000, 2000), "blue").save(content, "PNG")
    logo = api.owner.post(f"/api/projects/{context.project['id']}/logos", data={"name": "Large reference"},
                         files={"file": ("logo.png", content.getvalue(), "image/png")}).json()
    assert api.owner.post(f"/api/logos/{logo['id']}/approve").status_code == 200
    # Create a new run after the reference is approved; its snapshot is immutable.
    new_run = api.owner.post(f"/api/projects/{context.project['id']}/runs", json=context.body,
                            headers={"Idempotency-Key": "large-logo-run"}).json()
    from sqlalchemy import select
    with api.sessions.begin() as db:
        job = db.scalar(select(Job).where(Job.target_id == new_run["id"], Job.kind == "review"))
        job.state = "running"
    service = ToolService(api.settings, api.engine, api.storage)
    receipt = service.prepare_media(job.id)
    assert receipt["state"] == "completed", receipt
    derived = receipt["result"]["logos"][0]
    assert derived["source_file_id"] == logo["file_id"] and derived["file_id"] != logo["file_id"]
    with api.sessions() as db:
        original = db.get(FileRecord, logo["file_id"])
        assert api.storage.read(original.storage_key) == content.getvalue()
        assert derived["width"] * derived["height"] <= 2_000_000


def test_tool_receipt_is_idempotent_and_failed_calls_are_not_reissued(workflow):
    settings, engine, _, _, _, job = workflow
    service = ToolService(settings, engine)
    calls = []

    def action():
        calls.append(1)
        return {"value": "only once"}

    first = service.cached(job.id, "example", 0, action)
    second = service.cached(job.id, "example", 0, action)
    assert first == second
    assert calls == [1]
    failed = service.cached(job.id, "failure", 0, lambda: (_ for _ in ()).throw(ToolFailure("bounded", "실패")))
    replay = service.cached(job.id, "failure", 0, action)
    assert failed == replay and failed["state"] == "failed"
    assert calls == [1]


def test_lease_owner_cannot_finish_another_workers_job(workflow):
    _, _, sessions, _, _, job = workflow
    with sessions.begin() as db:
        db.get(Job, job.id).lease_token = "replacement"
    finish(sessions, job, error={"code": "worker_failed", "message": "old worker"})
    with sessions() as db:
        current = db.get(Job, job.id)
        assert current.state == "running" and current.error is None


def test_expired_cancelled_lease_is_not_reexecuted(workflow):
    _, _, sessions, _, _, job = workflow
    with sessions.begin() as db:
        current = db.get(Job, job.id)
        current.state, current.lease_until = "cancel_requested", utcnow() - timedelta(seconds=1)
    assert claim(sessions) is None
    with sessions() as db:
        assert db.get(Job, job.id).state == "cancelled"


def test_inference_profile_is_frozen_and_mismatch_stops_before_model(api):
    from dataclasses import replace
    from sqlalchemy import select
    from test_api import ready_run

    context = ready_run(api)
    assert context.run["snapshot"]["inference_profile"] == api.settings.inference_profile()
    with api.sessions.begin() as db:
        document_job = db.scalar(select(Job).where(Job.kind == "document", Job.project_id == context.project["id"]))
        assert document_job.payload["inference_profile"] == api.settings.inference_profile()
        job = db.scalar(select(Job).where(Job.kind == "review", Job.target_id == context.run["id"]))
        job.state = "running"
    changed = replace(api.settings, qwen_revision="different-declared-revision")
    service = ToolService(changed, api.engine, api.storage)
    with pytest.raises(ToolFailure) as error:
        service.infer(job.id, "inspect", 0)
    assert error.value.code == "inference_profile_changed"
    for operation, ordinal in (("inspect", 3), ("report", 1), ("extract_criteria", 1)):
        with pytest.raises(ToolFailure) as error:
            service.submit_inference(job.id, operation, ordinal)
        assert error.value.code == "invalid_operation"
