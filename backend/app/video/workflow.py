"""Version and decision records for project-linked video reviews."""
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.app.security.errors import AuthError

DECISION_STATES = {"approve_changes": "changes_approved", "approve_final": "final_approved"}


class VideoDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: UUID
    decision: Literal["approve_final", "approve_changes"]
    criteria_version: int = Field(ge=1)
    asset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(default="", max_length=500)
    human_checks: dict[str, str] = Field(default_factory=dict, max_length=101)


def prepare_review(job_id, project_id, actor_id, criteria, disclosure, parent, created_at):
    value = json.dumps([criteria, disclosure], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    criteria_sha256 = sha256(value.encode()).hexdigest()
    prior = parent["workflow"] if parent else None
    version = parent["criteria_version"] + (prior["criteria_sha256"] != criteria_sha256) if prior else 1
    workflow = {
        "project_id": project_id, "root_run_id": prior["root_run_id"] if prior else job_id,
        "parent_run_id": parent["id"] if parent else None,
        "asset_version": prior["asset_version"] + 1 if prior else 1,
        "criteria_sha256": criteria_sha256, "criteria_approved_by": actor_id,
        "criteria_approved_at": created_at, "human_decision": "unreviewed", "decisions": [],
    }
    return version, workflow


def final_requirements(job):
    """Pass or directly confirm an observed item. Unknown or failed evidence stays open."""
    results = {item["criterion_id"]: item for item in (job.get("review") or {}).get("results", [])}
    statuses = [(item["id"], results.get(item["id"], {})) for item in job.get("criteria", [])]
    disclosure = job.get("disclosure")
    if disclosure and disclosure.get("applicable"):
        statuses.append(("disclosure", {
            "status": disclosure.get("status"),
            "evidence_segments": any(rule.get("evidence") for rule in disclosure.get("rules", {}).values()),
        }))
    blocked = not statuses or not job.get("coverage")
    manual = []
    for criterion_id, item in statuses:
        if item.get("status") == "review" and item.get("evidence_segments") and not item.get("scope_limit"):
            manual.append(criterion_id)
        elif item.get("status") != "pass":
            blocked = True
    return {"blocked": blocked, "human_check_ids": manual}


def record_decision(job, command, actor_id, created_at):
    workflow = job["workflow"]
    payload = command.model_dump(mode="json")
    prior = next((row for row in workflow["decisions"] if row["command_id"] == payload["command_id"]), None)
    if prior:
        if prior["actor_id"] != actor_id or any(prior[key] != value for key, value in payload.items()):
            raise AuthError("IDEMPOTENCY_KEY_REUSED", 409)
        return False
    if job["status"] != "completed" or workflow["human_decision"] != "unreviewed":
        raise AuthError("INVALID_WORKFLOW_STATE", 409)
    if command.criteria_version != job["criteria_version"] or command.asset_sha256 != job["media"]["sha256"]:
        raise AuthError("VIDEO_REVIEW_VERSION_CHANGED", 409)
    if command.decision == "approve_changes":
        if not command.reason.strip() or command.human_checks:
            raise AuthError("INVALID_REQUEST", 422)
    else:
        requirements = final_requirements(job)
        if requirements["blocked"] or set(command.human_checks) != set(requirements["human_check_ids"]):
            raise AuthError("VIDEO_APPROVAL_EVIDENCE_INCOMPLETE", 409)
        if any(not note.strip() or len(note) > 500 for note in command.human_checks.values()):
            raise AuthError("INVALID_REQUEST", 422)
    workflow["decisions"].append({**payload, "actor_id": actor_id, "approved_at": created_at})
    workflow["human_decision"] = DECISION_STATES[command.decision]
    return True
