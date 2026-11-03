"""Behavioral HTTP/security checks, without a runtime model or external services."""

from copy import deepcopy
import hashlib
import io
from types import SimpleNamespace
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pymupdf
import pytest
from sqlalchemy import func, inspect, select

from backend.creative_checker.api import create_app
from backend.creative_checker.database import create_engine
from backend.creative_checker.models import (
    Approval, AssetVersion, AuthSession, CriterionSet, Document, FileRecord, Job, Outbox, Project, Run,
)
from backend.creative_checker.settings import Settings
from backend.creative_checker.storage import LocalStorage


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def image_bytes(color="red"):
    stream = io.BytesIO()
    with Image.new("RGB", (24, 16), color) as image:
        image.save(stream, format="PNG")
    return stream.getvalue()


def pdf_bytes():
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Exact event title")
        return document.tobytes()


def project(api, client=None):
    return checked((client or api.owner).post("/api/projects", json={"name": "Test project", "description": "Source-backed review"}), 201)


def member(api, project_id, name, role):
    return checked(api.owner.put(f"/api/projects/{project_id}/members", json={"user_id": api.users[name], "role": role}))


def document(api, project_id):
    value = checked(api.owner.post(f"/api/projects/{project_id}/documents", files={"file": ("brief.pdf", pdf_bytes(), "application/pdf")}), 201)
    # The separate worker's explicit boundary. These tests exercise HTTP/storage,
    # not model extraction; no successful model response is fabricated by the API.
    with api.sessions.begin() as db:
        row = db.get(Document, value["id"])
        row.status = "waiting_review"
        row.blocks = [{"id": "b1", "page": 1, "text": "Exact event title"}]
    return value


def criterion(**overrides):
    return {"id": str(uuid4()), "text": "Use the exact event title", "category": "creative", "required": True,
            "kind": "exact_text", "expected": "Exact event title", "target": "poster", "asset_ids": [],
            "source": {"kind": "document", "block_id": "b1", "page": 1, "quote": "Exact event title"},
            "supported": True, "notes": "", **overrides}


def criteria(api, project_id, document_id, items=None, approve=True, groups=None):
    body = {"document_id": document_id, "items": items or [criterion()], "groups": groups or []}
    value = checked(api.owner.post(f"/api/projects/{project_id}/criteria", json=body), 201)
    if approve:
        value = checked(api.owner.post(f"/api/criteria/{value['id']}/approve"))
    return value


def asset(api, project_id, client=None):
    return checked((client or api.owner).post(f"/api/projects/{project_id}/assets",
                   data={"name": "Poster", "kind": "poster"}, files={"file": ("poster.png", image_bytes(), "image/png")}), 201)


def ready_run(api, *, count=1, approve=True):
    p = project(api)
    d = document(api, p["id"])
    c = criteria(api, p["id"], d["id"], [criterion() for _ in range(count)], approve=approve)
    a = asset(api, p["id"])
    body = {"asset_version_id": a["versions"][0]["id"]}
    run = None
    if approve:
        run = checked(api.owner.post(f"/api/projects/{p['id']}/runs", json=body, headers={"Idempotency-Key": "initial-review"}), 201)
    return SimpleNamespace(project=p, document=d, criteria=c, asset=a, body=body, run=run)


def finish(api, run, *, state="waiting_review", verdict="pass", missing=False, execution="completed", attempt=None):
    with api.sessions.begin() as db:
        row = db.get(Run, run["id"])
        row.state, row.stage, row.verdict = state, "inspection_finished", verdict
        if attempt is not None:
            row.attempt = attempt
        row.results = [] if missing else [
            {"criterion_id": item["id"], "execution_status": execution,
             "verdict": verdict or "review", "expected": item["expected"], "observed": "Exact event title",
             "reason": "Explicit test worker result", "evidence": [], "limitations": []}
            for item in row.snapshot["items"]
        ]
        job = db.scalar(select(Job).where(Job.target_id == row.id, Job.kind == "review"))
        job.state = "failed" if state == "failed" else "cancelled" if state == "cancelled" else "completed"


def add_logo(api, project_id, *, approve=True):
    value = checked(api.owner.post(f"/api/projects/{project_id}/logos", data={"name": "Official logo"},
                    files={"file": ("logo.png", image_bytes("blue"), "image/png")}), 201)
    if approve:
        value = checked(api.owner.post(f"/api/logos/{value['id']}/approve"))
    return value


def test_auth_csrf_and_origin_are_enforced(api):
    anonymous = api.client()
    response = anonymous.get("/api/projects")
    assert response.status_code == 401
    assert set(response.json()) == {"error", "request_id"}
    assert response.json()["request_id"] == response.headers["x-request-id"]
    missing_csrf = api.owner.post("/api/projects", json={"name": "Denied", "description": ""}, headers={"X-CSRF-Token": ""})
    assert missing_csrf.status_code == 403
    assert api.owner.post("/api/projects", json={"name": "Denied"}, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert anonymous.post("/api/auth/login", json={"email": "owner@example.test", "password": api.password}, headers={"Origin": "null"}).status_code == 403
    session = checked(api.owner.get("/api/auth/me"))
    assert session["csrf_token"] == api.owner.headers["X-CSRF-Token"]
    assert api.owner.cookies.get("cc_session")


def test_logout_and_password_change_revoke_sessions(api):
    other_session = api.client("owner")
    checked(api.owner.post("/api/auth/password", json={"current_password": api.password, "new_password": "a different secure password"}))
    assert other_session.get("/api/auth/me").status_code == 401
    assert api.owner.get("/api/auth/me").status_code == 200
    checked(api.owner.post("/api/auth/logout"))
    assert api.owner.get("/api/auth/me").status_code == 401
    with api.sessions() as db:
        assert all(row.token_hash != other_session.cookies.get("cc_session") for row in db.scalars(select(AuthSession)))


def test_admin_accounts_do_not_bypass_project_membership(api):
    p = project(api)
    admin = api.client("admin")
    assert admin.get(f"/api/projects/{p['id']}").status_code == 404
    assert api.owner.get("/api/admin/users").status_code == 403
    user = checked(admin.post("/api/admin/users", json={"email": "new@example.test", "name": "New member", "password": "a sufficiently long password"}), 201)
    assert set(user) == {"id", "email", "name", "is_admin"}
    assert not user["is_admin"]
    assert admin.post("/api/admin/users", json={"email": "new@example.test", "name": "Duplicate", "password": "a sufficiently long password"}).status_code == 409


def test_project_roles_and_outside_files_are_enforced(api):
    context = ready_run(api)
    p, c = context.project, context.criteria
    member(api, p["id"], "editor", "editor")
    member(api, p["id"], "viewer", "viewer")
    editor, viewer, outsider = api.client("editor"), api.client("viewer"), api.client("outsider")
    assert viewer.get(f"/api/projects/{p['id']}/assets").status_code == 200
    assert viewer.post(f"/api/projects/{p['id']}/archive", json={"archived": True}).status_code == 403
    assert editor.post(f"/api/criteria/{c['id']}/approve").status_code == 403
    assert editor.put(f"/api/projects/{p['id']}/members", json={"user_id": api.users["viewer"], "role": "editor"}).status_code == 403
    file_id = context.asset["versions"][0]["file_id"]
    assert outsider.get(f"/api/files/{file_id}").status_code == 404
    assert outsider.get(f"/api/runs/{context.run['id']}").status_code == 404
    assert api.owner.put(f"/api/projects/{p['id']}/members", json={"user_id": api.users["owner"], "role": "viewer"}).status_code == 409


def test_schema_creation_is_only_explicit(api):
    engine = create_engine("sqlite://")
    settings = Settings(database_url="sqlite://", testing=True, cookie_secure=False, data_dir=api.tmp_path / "empty")
    try:
        with TestClient(create_app(settings, engine=engine, storage=api.storage)) as client:
            assert client.get("/api/health/live").status_code == 200
            assert client.get("/api/health/ready").status_code == 503
            assert not inspect(engine).has_table("cc_users")
    finally:
        engine.dispose()
    with pytest.raises(ValueError, match="SQLite"):
        Settings(database_url="sqlite://", testing=False)


def test_document_upload_and_criteria_approval_are_durable(api):
    p = project(api)
    d = document(api, p["id"])
    with api.sessions() as db:
        job = db.scalar(select(Job).where(Job.target_id == d["id"]))
        assert job.kind == "document" and job.state == "queued"
        assert job.payload == {"document_id": d["id"], "inference_profile": api.settings.inference_profile()}
    c = criteria(api, p["id"], d["id"], approve=False)
    a = asset(api, p["id"])
    assert api.owner.post(f"/api/projects/{p['id']}/runs", json={"asset_version_id": a["versions"][0]["id"]}, headers={"Idempotency-Key": "unapproved"}).status_code == 409
    checked(api.owner.post(f"/api/criteria/{c['id']}/approve"))
    with api.sessions() as db:
        job = db.scalar(select(Job).where(Job.kind == "approval", Job.target_id == d["id"]))
        assert job.payload["decision"] == "criteria_approved"
        assert job.payload["criteria_version_id"] == c["id"]
        assert db.scalar(select(func.count()).select_from(Approval).where(Approval.target_id == c["id"])) == 1
        revision = db.get(Project, p["id"]).graph_revision
        revisions = [row.payload["graph_revision"] for row in db.scalars(select(Outbox).where(Outbox.project_id == p["id"]))]
        assert sorted(revisions) == list(range(1, revision + 1))


def test_criteria_validate_provenance_groups_assets_and_limit(api):
    p, foreign = project(api), project(api)
    d = document(api, p["id"])
    foreign_asset = asset(api, foreign["id"])
    base = {"document_id": d["id"], "items": [criterion()], "groups": []}
    bad = deepcopy(base)
    bad["items"][0]["source"]["quote"] = "invented source quote"
    assert api.owner.post(f"/api/projects/{p['id']}/criteria", json=bad).status_code == 422
    bad = deepcopy(base)
    bad["items"][0]["asset_ids"] = [foreign_asset["id"]]
    assert api.owner.post(f"/api/projects/{p['id']}/criteria", json=bad).status_code == 404
    bad = deepcopy(base)
    bad["groups"] = [{"id": str(uuid4()), "operator": "all", "criterion_ids": [str(uuid4())]}]
    assert api.owner.post(f"/api/projects/{p['id']}/criteria", json=bad).status_code == 422
    too_many = {**base, "items": [criterion() for _ in range(129)]}
    error = api.owner.post(f"/api/projects/{p['id']}/criteria", json=too_many)
    assert error.status_code == 422 and "128" in error.json()["error"]["message"]


def test_image_upload_validates_actual_bytes_and_paths(api):
    p = project(api)
    path = f"/api/projects/{p['id']}/assets"
    for filename, data in (("fake.png", b"not an image"), ("wrong.jpg", image_bytes()), ("../escape.png", image_bytes()), ("active.svg", b"<svg/>")):
        assert api.owner.post(path, data={"name": "Invalid", "kind": "poster"}, files={"file": (filename, data, "image/png")}).status_code == 422
    with api.sessions() as db:
        assert db.scalar(select(func.count()).select_from(FileRecord).where(FileRecord.project_id == p["id"])) == 0
    assert not list(api.storage.root.rglob("*.png"))


def test_document_upload_rejects_unsafe_archives(api):
    p = project(api)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("../escape", "unsafe")
    response = api.owner.post(f"/api/projects/{p['id']}/documents", files={"file": ("unsafe.docx", stream.getvalue(), "application/zip")})
    assert response.status_code == 422
    with api.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Document).where(Document.project_id == p["id"])) == 0


def test_file_download_range_and_new_versions_preserve_original(api):
    p = project(api)
    a = asset(api, p["id"])
    first = a["versions"][0]
    content = api.owner.get(f"/api/files/{first['file_id']}")
    assert content.status_code == 200 and content.content == image_bytes()
    ranged = api.owner.get(f"/api/files/{first['file_id']}", headers={"Range": "bytes=3-8"})
    assert ranged.status_code == 206 and ranged.content == image_bytes()[3:9]
    assert ranged.headers["content-range"] == f"bytes 3-8/{len(image_bytes())}"
    suffix = api.owner.get(f"/api/files/{first['file_id']}", headers={"Range": "bytes=-5"})
    assert suffix.status_code == 206 and suffix.content == image_bytes()[-5:]
    assert api.owner.get(f"/api/files/{first['file_id']}", headers={"Range": "bytes=999999-"}).status_code == 416
    version = checked(api.owner.post(f"/api/assets/{a['id']}/versions", files={"file": ("revised.png", image_bytes("green"), "image/png")}), 201)
    assert version["version"] == 2 and version["file_id"] != first["file_id"]
    assert api.owner.get(f"/api/files/{first['file_id']}").content == image_bytes()
    with api.sessions() as db:
        record = db.get(FileRecord, first["file_id"])
        assert record.sha256 == hashlib.sha256(image_bytes()).hexdigest()


def test_orm_version_and_approved_criteria_are_immutable(api):
    context = ready_run(api)
    with api.sessions() as db:
        version = db.get(AssetVersion, context.run["asset_version_id"])
        version.filename = "overwritten.png"
        with pytest.raises(ValueError, match="immutable"):
            db.flush()
        db.rollback()
        criteria = db.get(CriterionSet, context.criteria["id"])
        criteria.items = [criterion(text="Silently edited")]
        with pytest.raises(ValueError, match="immutable"):
            db.flush()
        db.rollback()
        run = db.get(Run, context.run["id"])
        run.snapshot = {"replaced": True}
        with pytest.raises(ValueError, match="immutable"):
            db.flush()


def test_runs_are_idempotent_and_collision_is_rejected(api):
    context = ready_run(api)
    same = checked(api.owner.post(f"/api/projects/{context.project['id']}/runs", json=context.body, headers={"Idempotency-Key": "initial-review"}), 201)
    assert same["id"] == context.run["id"]
    assert api.owner.post(f"/api/projects/{context.project['id']}/runs", json=context.body).status_code == 422
    changed = {**context.body, "criteria_version_id": context.criteria["id"]}
    assert api.owner.post(f"/api/projects/{context.project['id']}/runs", json=changed, headers={"Idempotency-Key": "initial-review"}).status_code == 409
    member(api, context.project["id"], "editor", "editor")
    assert api.client("editor").post(f"/api/projects/{context.project['id']}/runs", json=context.body, headers={"Idempotency-Key": "initial-review"}).status_code == 409
    with api.sessions() as db:
        jobs = db.scalars(select(Job).where(Job.kind == "review", Job.project_id == context.project["id"])).all()
        assert len(jobs) == 1
        assert jobs[0].target_id == context.run["id"]


def test_cross_project_version_and_criteria_links_are_rejected(api):
    first, second = ready_run(api), ready_run(api)
    path = f"/api/projects/{first.project['id']}/runs"
    assert api.owner.post(path, json=second.body, headers={"Idempotency-Key": "foreign-version"}).status_code == 404
    assert api.owner.post(path, json={**first.body, "criteria_version_id": second.criteria["id"]}, headers={"Idempotency-Key": "foreign-criteria"}).status_code == 404
    assert api.owner.post(path, json={**first.body, "previous_run_id": second.run["id"]}, headers={"Idempotency-Key": "foreign-previous"}).status_code == 404


@pytest.mark.parametrize("state", ["queued", "running", "cancel_requested"])
def test_active_run_cannot_receive_final_approval(api, state):
    context = ready_run(api)
    with api.sessions.begin() as db:
        db.get(Run, context.run["id"]).state = state
    response = api.owner.post(f"/api/runs/{context.run['id']}/approve", json={"exception_reason": "Cannot skip active execution", "exception_criterion_ids": [context.criteria["items"][0]["id"]]})
    assert response.status_code == 409


@pytest.mark.parametrize("state", ["failed", "cancelled"])
@pytest.mark.parametrize("missing", [False, True])
def test_terminal_failed_or_missing_conditions_require_targeted_exceptions(api, state, missing):
    context = ready_run(api, count=2)
    finish(api, context.run, state=state, verdict=None, missing=missing, execution="failed", attempt=3)
    path = f"/api/runs/{context.run['id']}/approve"
    ids = [item["id"] for item in context.criteria["items"]]
    assert api.owner.post(path, json={}).status_code == 409
    assert api.owner.post(path, json={"exception_reason": "Reviewed the missing checks", "exception_criterion_ids": ids[:1]}).status_code == 409
    assert api.owner.post(path, json={"exception_reason": "Wrong target", "exception_criterion_ids": [str(uuid4())]}).status_code == 422
    approval = checked(api.owner.post(path, json={"exception_reason": "Explicitly accept these uninspected conditions", "exception_criterion_ids": ids}), 201)
    assert approval["original_verdict"] is None
    assert approval["snapshot"]["state"] == state
    run = checked(api.owner.get(f"/api/runs/{context.run['id']}"))
    assert run["state"] == state and run["verdict"] is None and run["attempt"] == 3
    with api.sessions() as db:
        job = db.scalar(select(Job).where(Job.kind == "approval", Job.target_id == context.run["id"]))
        assert job.attempt == 3 and job.payload["attempt"] == 3
        assert job.payload["decision"] == "final_approved"
    assert api.owner.post(f"/api/runs/{context.run['id']}/retry", headers={"Idempotency-Key": "cannot-reuse-approval"}).status_code == 409


def test_stale_criteria_block_old_run_approval(api):
    context = ready_run(api)
    finish(api, context.run)
    new_criteria = criteria(api, context.project["id"], context.document["id"])
    assert new_criteria["id"] != context.criteria["id"]
    response = api.owner.post(f"/api/runs/{context.run['id']}/approve", json={})
    assert response.status_code == 409 and response.json()["error"]["code"] == "stale_review"
    assert api.owner.post(f"/api/criteria/{context.criteria['id']}/approve").status_code == 409
    run = checked(api.owner.get(f"/api/runs/{context.run['id']}"))
    assert run["snapshot"]["criteria"]["id"] == context.criteria["id"]


def test_new_approved_logo_blocks_stale_run_and_fifth_logo(api):
    context = ready_run(api)
    finish(api, context.run)
    add_logo(api, context.project["id"])
    response = api.owner.post(f"/api/runs/{context.run['id']}/approve", json={})
    assert response.status_code == 409 and response.json()["error"]["code"] == "stale_logos"
    for _ in range(3):
        add_logo(api, context.project["id"])
    fifth = add_logo(api, context.project["id"], approve=False)
    error = api.owner.post(f"/api/logos/{fifth['id']}/approve")
    assert error.status_code == 409 and error.json()["error"]["code"] == "logo_limit"


def test_change_approval_connects_new_version_and_full_reinspection(api):
    context = ready_run(api, count=2)
    finish(api, context.run, verdict="fail", attempt=2)
    change = checked(api.owner.post(f"/api/runs/{context.run['id']}/change-requests", json={"message": "Please revise", "items": [{"criterion_id": context.criteria["items"][0]["id"], "message": "Correct the title"}]}), 201)
    path = f"/api/assets/{context.asset['id']}/versions"
    assert api.owner.post(path, data={"change_request_id": change["id"]}, files={"file": ("new.png", image_bytes(), "image/png")}).status_code == 409
    checked(api.owner.post(f"/api/change-requests/{change['id']}/approve"))
    with api.sessions() as db:
        job = db.scalar(select(Job).where(Job.kind == "approval", Job.target_id == context.run["id"]))
        assert job.attempt == 2 and job.payload["decision"] == "changes_approved"
    version = checked(api.owner.post(path, data={"change_request_id": change["id"]}, files={"file": ("new.png", image_bytes("green"), "image/png")}), 201)
    run = checked(api.owner.post(f"/api/projects/{context.project['id']}/runs", json={"asset_version_id": version["id"]}, headers={"Idempotency-Key": "full-reinspection"}), 201)
    assert run["previous_run_id"] == context.run["id"]
    assert len(run["snapshot"]["items"]) == 2
    assert run["snapshot"]["change_request_id"] == change["id"]


def test_cancelled_queue_retry_uses_new_attempt_and_frozen_input(api):
    context = ready_run(api)
    cancelled = checked(api.owner.post(f"/api/runs/{context.run['id']}/cancel"))
    assert cancelled["state"] == "cancelled"
    retried = checked(api.owner.post(f"/api/runs/{context.run['id']}/retry", headers={"Idempotency-Key": "retry-once"}))
    assert retried["attempt"] == 2 and retried["state"] == "queued"
    assert retried["snapshot"] == context.run["snapshot"]
    replay = checked(api.owner.post(f"/api/runs/{context.run['id']}/retry", headers={"Idempotency-Key": "retry-once"}))
    assert replay["attempt"] == 2
    with api.sessions() as db:
        jobs = db.scalars(select(Job).where(Job.kind == "review", Job.target_id == context.run["id"]).order_by(Job.attempt)).all()
        assert [job.attempt for job in jobs] == [1, 2]
        assert [job.state for job in jobs] == ["cancelled", "queued"]


def test_archived_project_is_read_only_and_can_be_restored(api):
    context = ready_run(api)
    checked(api.owner.post(f"/api/projects/{context.project['id']}/archive", json={"archived": True}))
    assert api.owner.get(f"/api/projects/{context.project['id']}/documents").status_code == 200
    assert api.owner.post(f"/api/runs/{context.run['id']}/cancel").status_code == 409
    checked(api.owner.post(f"/api/projects/{context.project['id']}/archive", json={"archived": False}))
    assert api.owner.post(f"/api/runs/{context.run['id']}/cancel").status_code == 200


def test_local_storage_rejects_overwrite_and_escaping_paths(tmp_path):
    storage = LocalStorage(tmp_path / "files")
    key = f"{uuid4()}/{uuid4()}"
    storage.put(key, b"immutable", "text/plain")
    with pytest.raises(FileExistsError):
        storage.put(key, b"replacement", "text/plain")
    assert storage.read(key) == b"immutable"
    for unsafe in ("../escape", "/absolute", "project/../../escape", "C:/escape", "project\\escape", "project//escape", "project/./escape"):
        with pytest.raises(ValueError):
            storage.read(unsafe)
        with pytest.raises(ValueError):
            storage.put(unsafe, b"denied")
        with pytest.raises(ValueError):
            storage.delete(unsafe)
    assert not (tmp_path / "escape").exists()
