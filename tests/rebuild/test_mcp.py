"""Real MCP transport and parser; no inference endpoint is called."""
import hashlib

from fastapi.testclient import TestClient
import fitz

from backend.creative_checker.database import Base, create_engine, session_factory
from backend.creative_checker.models import Document, FileRecord, Job, Project, User, new_id
from backend.creative_checker.settings import get_settings
from backend.creative_checker.storage import create_storage
from backend.creative_checker.tools import create_app


def test_mcp_token_boundary_and_real_document_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'mcp.sqlite').as_posix()}")
    monkeypatch.setenv("CC_TESTING", "1")
    monkeypatch.setenv("CC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CC_MCP_URL", "http://testserver/mcp")
    monkeypatch.setenv("CC_MCP_TOKEN", "test-only-service-token-32-characters")
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "The event title must appear.")
    content = pdf.tobytes()
    pdf.close()
    sessions = session_factory(engine)
    with sessions.begin() as db:
        user = User(id=new_id(), email="mcp@example.test", name="test", password_hash="unused")
        db.add(user)
        db.flush()
        project = Project(id=new_id(), name="MCP transport test", created_by=user.id)
        db.add(project)
        db.flush()
        key = f"{project.id}/document.pdf"
        create_storage(settings).put(key, content, "application/pdf")
        file = FileRecord(id=new_id(), project_id=project.id, storage_key=key, filename="document.pdf",
                          size=len(content), sha256=hashlib.sha256(content).hexdigest(), media_type="application/pdf")
        db.add(file)
        db.flush()
        document = Document(id=new_id(), project_id=project.id, version=1, file_id=file.id, filename=file.filename)
        db.add(document)
        db.flush()
        job = Job(id=new_id(), kind="document", project_id=project.id, target_id=document.id, state="running")
        db.add(job)
    headers = {"Authorization": f"Bearer {settings.mcp_token}", "Accept": "application/json, text/event-stream"}
    try:
        with TestClient(create_app()) as client:
            assert client.post("/mcp", json={}).status_code == 401
            response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
                "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "rebuild-contract-test", "version": "1"}}})
            assert response.status_code == 200, response.text
            listed = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()
            assert {tool["name"] for tool in listed["result"]["tools"]} == {
                "read_document", "prepare_media", "submit_inference", "get_inference", "read_evidence"}
            payload = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "read_document", "arguments": {"job_id": job.id}}}
            response = client.post("/mcp", headers=headers, json=payload)
            assert response.status_code == 200, response.text
            import json
            result = response.json()["result"]
            assert not result.get("isError"), result
            parsed = result.get("structuredContent") or json.loads(result["content"][0]["text"])
            assert parsed["state"] == "completed", parsed
            assert any("event title" in block["text"] for block in parsed["result"]["blocks"])
            second = client.post("/mcp", headers=headers, json=payload).json()["result"]
            repeated = second.get("structuredContent") or json.loads(second["content"][0]["text"])
            assert repeated["call_id"] == parsed["call_id"]
    finally:
        engine.dispose()
        get_settings.cache_clear()
