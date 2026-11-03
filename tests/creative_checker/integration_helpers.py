"""Isolated real-service harness with explicitly synthetic Qwen replies.

FastAPI, PostgreSQL transactions/leases, PostgresSaver, document/media parsers,
ToolService receipts, local file bytes and Neo4j use production code. Workflow
tools dispatch directly; mcp_client exercises the real HTTP/JSON-RPC ASGI
boundary. Separate-process network MCP/model evaluations remain separate.
"""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import io
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from PIL import Image, ImageDraw
import pymupdf
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from backend.creative_checker import orchestration, qwen, tools, worker
from backend.creative_checker.api import create_app
from backend.creative_checker.auth import password_hash
from backend.creative_checker.database import Base, create_engine, session_factory
from backend.creative_checker.graph import GraphStore
from backend.creative_checker.models import Job, User, new_id, utcnow
from backend.creative_checker.settings import Settings
from backend.creative_checker.storage import LocalStorage
from backend.creative_checker.tools import ToolService
from backend.creative_checker import tool_records  # Register the receipt table.


def integration_config():
    database = os.getenv("CC_INTEGRATION_DATABASE_URL")
    graph_uri = os.getenv("CC_TEST_NEO4J_URI")
    graph_password = os.getenv("CC_TEST_NEO4J_PASSWORD")
    if not database or not graph_uri or not graph_password:
        pytest.skip("Dedicated PostgreSQL/Neo4j integration endpoints and credentials are not configured")
    url, neo = make_url(database), urlsplit(graph_uri)
    if (url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "localhost"}
            or url.port != 25429 or url.database != "checker_rebuild"
            or neo.hostname not in {"127.0.0.1", "localhost"} or neo.port != 57629
            or neo.scheme not in {"bolt", "neo4j"}):
        pytest.fail("Integration tests accept only the dedicated local rebuild service ports", pytrace=False)
    return database, graph_uri, os.getenv("CC_TEST_NEO4J_USER", "neo4j"), graph_password


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def image_bytes(color):
    stream = io.BytesIO()
    with Image.new("RGB", (320, 160), color) as image:
        ImageDraw.Draw(image).text((20, 65), "Expected title" if color == "green" else "Different title", fill="white")
        image.save(stream, "PNG")
    return stream.getvalue()


def pdf_bytes():
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Expected title")
        return document.tobytes()


class SyntheticQwen:
    """Deterministic fixture responses, never an accuracy or model evaluation."""

    def __init__(self, calls):
        self.calls = calls
        self.stats = {"synthetic": True, "http_attempts": 0}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def extract_criteria(self, blocks):
        self.calls.append("extract_criteria")
        block = next(item for item in blocks if "Expected title" in item["text"])
        return {"items": [{"id": new_id(), "text": "Use the exact title", "category": "creative",
            "required": True, "kind": "exact_text", "expected": "Expected title", "target": "poster", "asset_ids": [],
            "source": {"kind": "document", "block_id": block["id"], "page": block["page"], "quote": "Expected title"},
            "supported": True, "notes": "Synthetic model reply for database workflow testing"}], "groups": []}

    def inspect(self, criteria, images, *, logos):
        self.calls.append("inspect")
        frame = images[0]
        # An explicit fixture marker selects the reply. This does not measure
        # image understanding or independently establish visual compliance.
        with Image.open(io.BytesIO(frame["data"])) as image:
            red, green, _blue = image.convert("RGB").getpixel((0, 0))
        observed = "Expected title" if green > red else "Different title"
        return {"results": [{"criterion_id": item["id"], "execution_status": "completed",
            "verdict": "pass" if green > red else "fail", "observed": observed, "expected": deepcopy(item["expected"]),
            "reason": "Synthetic fixture observation; not model evaluation", "limitations": [],
            "evidence": [{"file_id": frame["file_id"], "frame_ms": frame["frame_ms"],
                          "bbox": [0.0, 0.0, 1.0, 1.0], "text": observed}]} for item in criteria]}

    def report(self, results):
        self.calls.append("report")
        return {"summary": "SYNTHETIC integration fixture report", "draft": {"message": "Synthetic correction draft",
                "items": [{"criterion_id": item["criterion_id"], "message": "Use the approved title", "evidence_ids": []}
                          for item in results if item["verdict"] != "pass"]}}


class IsolatedServices:
    def __init__(self, config, root: Path, monkeypatch):
        database, graph_uri, graph_user, graph_password = config
        self.schema = "cc_it_" + uuid4().hex
        # A single option survives both SQLAlchemy and libpq URI parsing. Their
        # treatment of '+' for spaces differs for a multi-option query value.
        url = make_url(database).update_query_dict({"options": f"-csearch_path={self.schema}"})
        self.settings = Settings(database_url=url.render_as_string(hide_password=False), data_dir=root / "data",
            cookie_secure=False, testing=False, neo4j_uri=graph_uri, neo4j_user=graph_user, neo4j_password=graph_password,
            qwen_base_url="http://synthetic.invalid", qwen_revision="synthetic-integration-fixture")
        self.admin = create_engine(database, connect_args={"connect_timeout": 5})
        self.engine = create_engine(self.settings.database_url, connect_args={"connect_timeout": 5})
        self.sessions = session_factory(self.engine)
        self.storage = LocalStorage(root / "files")
        self.graph = GraphStore(self.settings)
        self.monkeypatch = monkeypatch
        self.stack = ExitStack()
        self.project_ids = set()
        self.synthetic_calls = []
        self.synthetic_lock_key = int(uuid4().hex[:15], 16)
        self.schema_created = False

    def __enter__(self):
        try:
            with self.admin.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            self.schema_created = True
            with self.engine.connect() as connection:
                assert connection.scalar(text("SELECT current_schema()")) == self.schema
            Base.metadata.create_all(self.engine)
            with PostgresSaver.from_conn_string(worker.checkpoint_dsn(self.settings.database_url)) as saver:
                saver.setup()
            operator_email = f"integration-{uuid4().hex}@example.test"
            password = "synthetic integration operator password"
            with self.sessions.begin() as db:
                db.add(User(email=operator_email, name="Integration operator", password_hash=password_hash(password), is_admin=True))
            self.http = self.stack.enter_context(TestClient(create_app(self.settings, engine=self.engine, storage=self.storage)))
            login = checked(self.http.post("/api/auth/login", json={"email": operator_email, "password": password}))
            self.http.headers["X-CSRF-Token"] = login["csrf_token"]
            self.service = ToolService(self.settings, self.engine, self.storage)
            # PostgreSQL advisory locks are database-wide, unlike search_path.
            # A private key avoids contending with the main agent's live GPU job.
            self.service.gpu_slot = self.synthetic_slot
            self.monkeypatch.setattr(qwen, "QwenClient", lambda *args, **kwargs: SyntheticQwen(self.synthetic_calls))

            async def dispatch(_settings, name, arguments):
                assert name in {"read_document", "prepare_media", "submit_inference", "get_inference", "read_evidence"}
                return getattr(self.service, name)(**arguments)

            self.monkeypatch.setattr(orchestration, "call_tool", dispatch)
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()

    def close(self):
        failures = []
        try:
            self.stack.close()
            # Only UUIDs returned by this schema's project creation are deleted.
            for project_id in self.project_ids:
                try:
                    self.graph.delete_project(project_id)
                except Exception:
                    failures.append("Neo4j cleanup failed for test project " + project_id)
        finally:
            self.graph.close()
            self.engine.dispose()
            try:
                if self.schema_created:
                    assert self.schema.startswith("cc_it_") and len(self.schema) == 38
                    with self.admin.begin() as connection:
                        connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
                    self.schema_created = False
            finally:
                self.admin.dispose()
        if failures:
            raise RuntimeError("; ".join(failures))

    @contextmanager
    def synthetic_slot(self):
        with self.engine.connect() as connection:
            assert connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": self.synthetic_lock_key})
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.synthetic_lock_key})
                connection.commit()

    def project(self):
        project = checked(self.http.post("/api/projects", json={"name": "Isolated real-service integration", "description": "Synthetic Qwen replies"}), 201)
        self.project_ids.add(project["id"])
        return project

    def document(self, project_id):
        return checked(self.http.post(f"/api/projects/{project_id}/documents",
                       files={"file": ("brief.pdf", pdf_bytes(), "application/pdf")}), 201)

    def approved_document(self):
        project = self.project()
        document = self.document(project["id"])
        job = self.step("document", document["id"])
        assert self.checkpoint(job).next == ("criteria_gate",)
        document = next(item for item in checked(self.http.get(f"/api/projects/{project['id']}/documents"))["items"] if item["id"] == document["id"])
        assert document["status"] == "waiting_review" and document["blocks"] and document["criteria"]
        criteria = checked(self.http.post(f"/api/projects/{project['id']}/criteria", json={
            "document_id": document["id"], "items": document["criteria"], "groups": document["groups"],
        }), 201)
        criteria = checked(self.http.post(f"/api/criteria/{criteria['id']}/approve"))
        self.step("approval", document["id"])
        assert self.checkpoint(job).next == ()
        return project, document, criteria

    def step(self, kind, target_id):
        job = worker.claim(self.sessions)
        assert job is not None and job.kind == kind and job.target_id == target_id
        self.process(job)
        with self.sessions() as db:
            saved = db.get(Job, job.id)
            assert saved.state == "completed", saved.error
        return job

    def process(self, job, *, fresh_engine=False):
        engine = create_engine(self.settings.database_url) if fresh_engine else self.engine
        sessions = session_factory(engine) if fresh_engine else self.sessions
        try:
            # Fresh saver/connection on every call; recovery reads durable state.
            with PostgresSaver.from_conn_string(worker.checkpoint_dsn(self.settings.database_url)) as saver:
                worker.process(self.settings, sessions, saver, job)
        finally:
            if fresh_engine:
                engine.dispose()

    def checkpoint(self, job):
        with PostgresSaver.from_conn_string(worker.checkpoint_dsn(self.settings.database_url)) as saver:
            graph = orchestration.build_graph(self.settings, self.sessions, saver)
            return graph.get_state({"configurable": {"thread_id": worker.thread_id(job)}})

    def expire(self, job_id):
        with self.sessions.begin() as db:
            db.get(Job, job_id).lease_until = utcnow() - timedelta(seconds=1)

    @contextmanager
    def mcp_client(self):
        """Real FastMCP/Bearer/JSON-RPC ASGI boundary using this isolated service."""
        settings = replace(self.settings, mcp_url="http://testserver/mcp", mcp_token=secrets.token_urlsafe(32))
        with self.monkeypatch.context() as patch:
            patch.setattr(tools, "get_settings", lambda: settings)
            # Share the actual ToolService and its private storage/lock namespace.
            # The production protocol, argument validation and lease checks run.
            patch.setattr(tools, "ToolService", lambda _settings: self.service)
            with TestClient(tools.create_app()) as client:
                headers = {"Authorization": f"Bearer {settings.mcp_token}", "Accept": "application/json, text/event-stream"}
                initialized = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
                    "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "isolated-lease-integration", "version": "1"}}})
                assert initialized.status_code == 200, initialized.text
                yield client, headers
