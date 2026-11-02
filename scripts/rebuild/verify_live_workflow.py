"""Opt-in real PostgreSQL, Neo4j, MCP and Qwen workflow; creates synthetic inputs only.

Run with the rebuild environment, then inspect report.json. Data is retained in a
unique schema and output directory. No model output or business result is mocked.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
import pymupdf
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from backend.creative_checker.api import create_app
from backend.creative_checker.auth import password_hash
from backend.creative_checker.cli import initialize_database
from backend.creative_checker.database import create_engine, session_factory
from backend.creative_checker.graph import GraphStore
from backend.creative_checker.media import _binary
from backend.creative_checker.models import User
from backend.creative_checker.projection import deliver_one
from backend.creative_checker.settings import get_settings
from backend.creative_checker.tool_records import ToolCall


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.qwen_base_url or settings.qwen_revision == "unverified":
        parser.error("Configure the real Qwen endpoint and its verified deployed revision first")
    if settings.testing or settings.storage_backend != "local":
        parser.error("This test needs real PostgreSQL and an isolated local file store")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    schema = "cc_live_" + uuid4().hex
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "schema": schema,
              "synthetic_inputs": True, "mock_model": False, "outcome": "running", "steps": [], "runs": []}

    def record(stage, **facts):
        report["steps"].append({"stage": stage, **facts})
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"stage": stage, **facts}, ensure_ascii=False), flush=True)

    admin = create_engine(settings.database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        mcp_port = listener.getsockname()[1]
    os.environ.update(
        DATABASE_URL=make_url(settings.database_url).update_query_dict(
            {"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False),
        CC_DATA_DIR=str(output / "files"), CC_COOKIE_SECURE="false",
        CC_MCP_URL=f"http://127.0.0.1:{mcp_port}/mcp")
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_engine(settings.database_url)
    sessions = session_factory(engine)
    initialize_database(engine, settings)
    graph = GraphStore(settings)
    graph.initialize()
    email, password = f"live-{uuid4().hex}@example.test", secrets.token_urlsafe(24)
    with sessions.begin() as db:
        user = User(email=email, name="실제 모델 연결 검증 계정", password_hash=password_hash(password), is_admin=True)
        db.add(user)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    mcp_log = (output / "mcp.log").open("w", encoding="utf-8")
    mcp = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.creative_checker.tools:create_app",
        "--factory", "--host", "127.0.0.1", "--port", str(mcp_port), "--no-access-log"],
        stdout=mcp_log, stderr=subprocess.STDOUT, creationflags=flags)
    report["model_profile"] = settings.inference_profile()

    def worker_once():
        with (output / "worker.log").open("a", encoding="utf-8") as log:
            completed = subprocess.run([sys.executable, "-m", "backend.creative_checker.worker", "--once"],
                stdout=log, stderr=subprocess.STDOUT, timeout=900, creationflags=flags)
        assert completed.returncode == 0, "Worker CLI failed; inspect the local worker log"

    def wait_for(fetch, predicate):
        for _ in range(16):
            value = fetch()
            if predicate(value):
                return value
            if value.get("state", value.get("status")) in {"failed", "cancelled"}:
                report["failed_entity"] = value
                raise AssertionError("The real workflow failed; see failed_entity in report.json")
            worker_once()
        raise AssertionError("The bounded worker loop did not reach its expected state")

    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                # An unauthenticated request must be rejected by the actual MCP server.
                response = httpx.get(settings.mcp_url, timeout=2, trust_env=False)
                assert response.status_code == 401
                break
            except httpx.TransportError:
                if mcp.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("MCP server did not start")
                time.sleep(0.2)
        record("mcp_ready", authentication_enforced=True)

        font_path = next((p for p in [Path("C:/Windows/Fonts/malgun.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")] if p.exists()), None)
        assert font_path, "A Korean font is required for the synthetic fixture"
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_font(fontname="fixture", fontfile=str(font_path))
            page.insert_text((45, 70), "검증용 가상 행사 제작 기준\n포스터와 영상에 다음 두 조건을 모두 반드시 표시한다.\n"
                "1. 행사명은 정확히 '봄빛 축제'로 표시한다.\n2. 행사 날짜는 '2026-11-13'으로 표시한다.",
                fontname="fixture", fontsize=14)
            pdf.save(output / "brief.pdf")
        for variant, date in (("before", "2026-11-14"), ("after", "2026-11-13")):
            picture = Image.new("RGB", (960, 640), "white")
            drawing = ImageDraw.Draw(picture)
            drawing.text((105, 180), "봄빛 축제", font=ImageFont.truetype(str(font_path), 105), fill="black")
            drawing.text((125, 340), date, font=ImageFont.truetype(str(font_path), 82), fill="black")
            picture.save(output / f"{variant}.png")
            subprocess.run([_binary("ffmpeg"), "-v", "error", "-loop", "1", "-i", str(output / f"{variant}.png"),
                "-t", "6", "-r", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output / f"{variant}.mp4")],
                check=True, timeout=60, creationflags=flags)

        with TestClient(create_app(settings, engine=engine)) as api:
            def request(method, url, expected=200, **kwargs):
                response = api.request(method, "/api" + url, **kwargs)
                if response.status_code != expected:
                    error = response.json().get("error", {})
                    raise AssertionError(f"{method} {url} returned {response.status_code}: {error.get('code')}")
                return response.json()

            def upload(url, path, **data):
                with path.open("rb") as content:
                    return request("POST", url, 201, data=data, files={"file": (path.name, content)})

            auth = request("POST", "/auth/login", json={"email": email, "password": password})
            api.headers["X-CSRF-Token"] = auth["csrf_token"]
            project = request("POST", "/projects", 201, json={"name": "실모델 연동 검증 · 가상 행사", "description": schema})
            project_id = report["project_id"] = project["id"]
            prefix = f"/projects/{project_id}"
            document = upload(prefix + "/documents", output / "brief.pdf")
            document = wait_for(lambda: next(d for d in request("GET", prefix + "/documents")["items"]
                                            if d["id"] == document["id"]), lambda d: d["status"] == "waiting_review")
            assert document["criteria"], "Qwen returned no document criteria"
            report["extracted_document"] = document
            record("document_extracted", criteria_count=len(document["criteria"]))
            criteria = request("POST", prefix + "/criteria", 201, json={"document_id": document["id"],
                "items": document["criteria"], "groups": document.get("groups", [])})
            criteria = request("POST", f"/criteria/{criteria['id']}/approve")
            worker_once()
            record("criteria_approved", criteria_id=criteria["id"])

            for kind, extension in (("poster", "png"), ("video", "mp4")):
                asset = upload(prefix + "/assets", output / f"before.{extension}", name=f"가상 행사 {kind}", kind=kind)

                def inspect_version(version_id, previous=None):
                    run = request("POST", prefix + "/runs", 201, headers={"Idempotency-Key": uuid4().hex},
                        json={"asset_version_id": version_id, "previous_run_id": previous})
                    record("run_started", kind=kind, run_id=run["id"])
                    run = wait_for(lambda: request("GET", f"/runs/{run['id']}"), lambda r: r["state"] == "waiting_review")
                    assert run["results"] and run["report"], "Actual observations and report required"
                    assert any(r.get("evidence") for r in run["results"]), "No actual evidence returned"
                    for result in run["results"]:
                        for evidence in result["evidence"]:
                            assert api.get(f"/api/files/{evidence['file_id']}").status_code == 200
                    report["runs"].append(run)
                    record("run_inspected", kind=kind, run_id=run["id"], verdict=run["verdict"])
                    return run

                initial = inspect_version(asset["versions"][0]["id"])
                change = request("POST", f"/runs/{initial['id']}/change-requests", 201, json={
                    "items": [{"criterion_id": item["id"], "message": "검증용 날짜를 2026-11-13으로 수정합니다."}
                              for item in initial["snapshot"]["items"]],
                    "message": "검증용 가상 산출물의 날짜를 기준에 맞게 수정합니다."})
                request("POST", f"/change-requests/{change['id']}/approve")
                worker_once()
                version = upload(f"/assets/{asset['id']}/versions", output / f"after.{extension}", change_request_id=change["id"])
                final = inspect_version(version["id"], initial["id"])
                assert {x["id"] for x in final["snapshot"]["items"]} == {x["id"] for x in initial["snapshot"]["items"]}
                assert final["snapshot"]["asset_version"]["version"] == 2
                # This is an explicitly labelled test approval, not a claim of model correctness.
                reason = "가상 자료를 이용한 연동 검증 승인. 영상 미관측 구간과 모델 불확실성은 원판정에 보존합니다."
                approval = request("POST", f"/runs/{final['id']}/approve", 201,
                    json={"exception_reason": reason, "exception_criterion_ids": [x["id"] for x in final["snapshot"]["items"]]})
                worker_once()
                saved = request("GET", f"/runs/{final['id']}")
                assert saved["state"] == "completed" and saved["verdict"] == final["verdict"]
                assert approval["original_verdict"] == final["verdict"]
                record("reinspection_approved", kind=kind, run_id=final["id"], original_verdict=final["verdict"])

            for _ in range(100):
                if not deliver_one(sessions, graph):
                    break
            relations = request("GET", prefix + "/graph")
            assert relations["status"] == "synced" and relations["nodes"] and relations["edges"]
            impact = request("GET", prefix + "/impact", params={"criterion_id": criteria["items"][0]["id"]})
            assert impact["items"], "Neo4j must return actual affected runs"
            history = request("GET", prefix + "/history")
            assert len([a for a in history["approvals"] if a["target_type"] == "run"]) == 2
            with sessions() as db:
                calls = db.scalars(select(ToolCall).where(ToolCall.project_id == project_id)).all()
                report["tool_calls"] = [{"operation": c.operation, "state": c.state,
                    "usage": (c.result or {}).get("usage"), "error": c.error} for c in calls]
                assert all(c.state == "completed" for c in calls)
            report["outcome"] = "workflow_passed_model_accuracy_not_established"
            record("complete", graph_nodes=len(relations["nodes"]), graph_edges=len(relations["edges"]),
                   impacted_runs=len(impact["items"]), approval_count=len(history["approvals"]))
    except Exception as exc:
        report["outcome"] = "failed"
        report["error_type"] = type(exc).__name__
        record("failed", error_type=type(exc).__name__)
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        mcp.terminate()
        try:
            mcp.wait(timeout=10)
        except subprocess.TimeoutExpired:
            mcp.kill()
            mcp.wait(timeout=10)
        mcp_log.close()
        graph.close()
        engine.dispose()


if __name__ == "__main__":
    main()
