"""One durable LangGraph for document approval and versioned asset inspection."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import math
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .inspection import aggregate_verdict, validate_results
from .models import Document, Job, Project, Run
from .prompts import MAX_IMAGES_PER_CALL
from .services import record_event
from .tools import ToolFailure, call_tool


class WorkflowState(TypedDict, total=False):
    job_id: str
    kind: str
    target_id: str
    attempt: int
    manifest: dict
    batch: int
    observations: list


def build_graph(settings, sessions, checkpointer, invoke_tool=None, *, lease_token=None):
    invoke_tool = invoke_tool or (lambda name, args: asyncio.run(call_tool(settings, name, args)))

    def invoke(state, name, **arguments):
        with sessions() as db:
            check(state, db)
        if lease_token is not None:
            arguments["lease_token"] = lease_token
        receipt = invoke_tool(name, {"job_id": state["job_id"], **arguments})
        if receipt.get("state") == "running":
            raise ToolFailure("ambiguous_tool_receipt", "이전 호출 완료 여부를 확인할 수 없습니다. 자동 재호출을 중단했습니다.")
        if receipt.get("state") != "completed":
            error = receipt.get("error") or {}
            raise ToolFailure(error.get("code", "tool_failed"), error.get("message", "도구 실행이 완료되지 않았습니다."))
        return receipt["result"]

    def check(state, db):
        job = db.get(Job, state["job_id"])
        if job is None or job.state != "running" or (lease_token and job.lease_token != lease_token):
            raise ToolFailure("cancelled", "작업 실행이 중단되었습니다.")
        return job

    def document(state):
        extracted = invoke(state, "read_document")
        with sessions.begin() as db:
            check(state, db)
            item = db.get(Document, state["target_id"])
            item.blocks, item.status = extracted["blocks"], "running"
            item.errors = [{"code": "extraction_warning", "message": str(w)} for w in extracted.get("warnings", [])]
        result = invoke(state, "submit_inference", operation="extract_criteria")
        with sessions.begin() as db:
            check(state, db)
            item = db.get(Document, state["target_id"])
            item.criteria, item.groups, item.status = result["items"], result.get("groups", []), "waiting_review"
            record_event(db, db.get(Project, item.project_id), "document.extracted", None,
                         {"document_id": item.id, "criteria_count": len(item.criteria)})
        return {}

    def criteria_gate(state):
        decision = interrupt({"kind": "criteria_approval", "document_id": state["target_id"]})
        if decision.get("decision") != "criteria_approved":
            raise ToolFailure("invalid_resume", "조건 승인 기록이 필요합니다.")
        with sessions.begin() as db:
            db.get(Document, state["target_id"]).status = "completed"
        return {}

    def prepare(state):
        result = invoke(state, "prepare_media")
        if not result.get("frames"):
            raise ToolFailure("empty_media", "검사할 이미지가 없습니다.")
        with sessions.begin() as db:
            check(state, db)
            run = db.get(Run, state["target_id"])
            run.stage = "inspect"
            run.progress = {"completed": 0, "total": math.ceil(len(result["frames"]) / MAX_IMAGES_PER_CALL), "unit": "batch"}
        return {"manifest": result, "batch": 0, "observations": []}

    def inspect_batch(state):
        with sessions() as db:
            check(state, db)
        result = invoke(state, "submit_inference", operation="inspect", batch_index=state["batch"])
        next_batch = state["batch"] + 1
        with sessions.begin() as db:
            check(state, db)
            run = db.get(Run, state["target_id"])
            run.progress = {"completed": next_batch, "total": math.ceil(len(state["manifest"]["frames"]) / MAX_IMAGES_PER_CALL), "unit": "batch"}
        return {"observations": state["observations"] + result["results"], "batch": next_batch}

    def queue_yield(state):
        interrupt({"kind": "queue_yield", "batch": state["batch"]})
        return {}

    def verify(state):
        manifest = state["manifest"]
        frames = {f["file_id"]: f for f in [*manifest["frames"], *manifest.get("logos", [])]}
        # A06 reads the stored bytes through MCP as well as checking their schema.
        for file_id in frames:
            evidence = invoke_tool("read_evidence", {"job_id": state["job_id"], "file_id": file_id})
            if evidence.get("file_id") != file_id or evidence.get("frame_ms") != frames[file_id]["frame_ms"]:
                raise ToolFailure("evidence_unavailable", "등록한 검사 근거를 다시 읽을 수 없습니다.")
        with sessions.begin() as db:
            check(state, db)
            run = db.get(Run, state["target_id"])
            items = run.snapshot["items"]
            logo_ids = {logo["file_id"] for logo in manifest.get("logos", [])}
            observations = deepcopy(state["observations"])
            for result in observations:
                result["scope"] = {"asset_kind": run.snapshot["asset"]["kind"], "asset_ids": [run.snapshot["asset"]["id"]],
                                   "complete": manifest["coverage"]["complete"], "all_frames_checked": False}
                result["logo_reference_ids"] = sorted(logo_ids)
                for ev in result.get("evidence", []):
                    if ev.get("file_id") in frames and ev.get("frame_ms") != frames[ev["file_id"]]["frame_ms"]:
                        result["verdict"] = "review"
                        result.setdefault("limitations", []).append("evidence_timestamp_mismatch")
            run.results = validate_results(items, observations, set(frames) | logo_ids)
            run.verdict = aggregate_verdict(items, run.results, groups=run.snapshot.get("groups", []))
            run.stage = "report"
            run.report = {"coverage": manifest["coverage"], "warnings": manifest.get("warnings", [])}
            record_event(db, db.get(Project, run.project_id), "run.validated", None,
                         {"attempt": run.attempt, "verdict": run.verdict, "coverage": manifest["coverage"]}, run.id)
        return {}

    def report(state):
        result = invoke(state, "submit_inference", operation="report")
        with sessions.begin() as db:
            check(state, db)
            run = db.get(Run, state["target_id"])
            run.report = {**(run.report or {}), **result}
            run.state, run.stage = "waiting_review", "human_review"
            record_event(db, db.get(Project, run.project_id), "run.awaiting_review", None,
                         {"attempt": run.attempt}, run.id)
        return {}

    def review_gate(state):
        decision = interrupt({"kind": "review_approval", "run_id": state["target_id"], "attempt": state["attempt"]})
        if decision.get("decision") not in {"changes_approved", "final_approved"}:
            raise ToolFailure("invalid_resume", "확정한 수정 요청 또는 최종 승인 기록이 필요합니다.")
        with sessions.begin() as db:
            run = db.get(Run, state["target_id"])
            if run.state == "waiting_review":
                run.state, run.stage = "completed", "completed"
        # Failure/cancellation and the machine verdict survive exception approval.
        return {}

    graph = StateGraph(WorkflowState)
    for name, node in [("document", document), ("criteria_gate", criteria_gate), ("prepare", prepare),
                       ("inspect", inspect_batch), ("queue_yield", queue_yield), ("verify", verify),
                       ("report", report), ("review_gate", review_gate)]:
        graph.add_node(name, node)
    graph.add_conditional_edges(START, lambda s: "document" if s["kind"] == "document" else "review_gate" if s["kind"] == "exception" else "prepare")
    graph.add_edge("document", "criteria_gate")
    graph.add_edge("criteria_gate", END)
    graph.add_edge("prepare", "inspect")
    graph.add_conditional_edges("inspect", lambda s: "queue_yield" if s["batch"] * MAX_IMAGES_PER_CALL < len(s["manifest"]["frames"]) else "verify")
    graph.add_edge("queue_yield", "inspect")
    graph.add_edge("verify", "report")
    graph.add_edge("report", "review_gate")
    graph.add_edge("review_gate", END)
    return graph.compile(checkpointer=checkpointer)
