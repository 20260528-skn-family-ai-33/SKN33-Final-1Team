"""Explicit development rehearsal of configured approval/re-review workflows.

Approvals are scripted test inputs. The command may invoke real developer-owned
adapters if explicitly configured; it never proves authenticated human approval,
model accuracy or production connectivity. No retries, queues or DB writes are
introduced here. Workflow state remains in memory.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .composition import TeamConfiguration, build_agents, describe_configuration
from .contracts import Approval, AssetRef, Context, Contract, DocumentRef, Identifier, Verdict
from .runtime import Workflow


class FlowCheckCase(Contract):
    id: Identifier
    context: Context
    document: DocumentRef
    asset: AssetRef
    revision_context: Context
    revision_asset: AssetRef
    expected_initial_verdict: Verdict
    # Normal final approval requires pass; exception approval is a separate policy.
    expected_revised_verdict: Literal["pass"] = "pass"
    change_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def fixed_scope(self):
        old, new = self.context, self.revision_context
        if (old.parent_run_id is not None or new.parent_run_id != old.run_id
                or old.run_id == new.run_id or old.project_id != new.project_id
                or old.document_version_id != new.document_version_id
                or old.criteria_version_id != new.criteria_version_id
                or self.document.document_version_id != old.document_version_id
                or old.asset_version_id != self.asset.asset_version_id
                or new.asset_version_id != self.revision_asset.asset_version_id
                or self.asset.asset_version_id == self.revision_asset.asset_version_id):
            raise ValueError("a flow check requires fixed document/criteria and a new child run/poster version")
        if not self.change_reason.strip():
            raise ValueError("scripted change approval requires a reason")
        return self


class FlowCaseError(ValueError):
    """Malformed local test input, detected before any configured factory runs."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("nonstandard JSON constant")


def load_flow_case(path: str | Path) -> FlowCheckCase:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"),
                           object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        return FlowCheckCase.model_validate(value)
    except (ValueError, TypeError):
        raise FlowCaseError("The workflow fixture is invalid.") from None


def _digest(value) -> str:
    raw = value.model_dump(mode="json") if isinstance(value, Contract) else value
    return sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def run_flow_check(case: FlowCheckCase, configuration: TeamConfiguration) -> dict:
    """Run one fixed six-action rehearsal, stopping at the first discrepancy.

    Revalidate copied inputs before importing any factory. Report accepted
    result hashes and timing/error metadata, never raw media or role payloads.
    """
    case = FlowCheckCase.model_validate(case.model_dump(mode="python", warnings=False)).model_copy(deep=True)
    configuration = TeamConfiguration.model_validate(configuration.model_dump(mode="python", warnings=False))
    workflow = Workflow(build_agents(configuration), profile=configuration.profile)
    original, revised = case.context, case.revision_context
    actions = []
    stopped = False
    unknown = False
    parent_snapshot = None
    roles = ("A02", "A04_A05", "A06", "A07")

    def approve(context, decision, **fields):
        return workflow.approve(context.run_id, Approval(context=context,
            actor_id=context.actor_id, decision=decision, **fields))

    plan = [
        ("start", original, "waiting_criteria", None, "unreviewed",
         lambda: workflow.start(original, case.document, case.asset)),
        ("approve_criteria", original, "waiting_review", case.expected_initial_verdict, "unreviewed",
         lambda: approve(original, "approve_criteria")),
        ("approve_changes", original, "waiting_revision", case.expected_initial_verdict, "changes_approved",
         lambda: approve(original, "approve_changes", reason=case.change_reason)),
        ("rereview", revised, "waiting_criteria", None, "unreviewed",
         lambda: workflow.rereview(original.run_id, revised, case.revision_asset)),
        ("approve_revised_criteria", revised, "waiting_review", case.expected_revised_verdict, "unreviewed",
         lambda: approve(revised, "approve_criteria")),
        ("approve_final", revised, "completed", case.expected_revised_verdict, "final_approved",
         lambda: approve(revised, "approve_final")),
    ]
    for action, context, expected_status, expected_verdict, expected_human, execute in plan:
        item = {"action": action, "run_id": context.run_id}
        if stopped:
            actions.append({**item, "status": "skipped", "reason": "PRIOR_FLOW_FAILURE"})
            continue
        # Workflow handles adapter failures and sanitizes their errors. Only
        # local workflow conflicts reach this boundary; never emit their text.
        try:
            snapshot = execute()
        except Exception:
            actions.append({**item, "status": "failed", "reason": "WORKFLOW_ACTION_FAILED"})
            stopped = True
            continue
        records = snapshot.step_records
        item.update(execution_status=snapshot.execution_status,
                    machine_verdict=snapshot.machine_verdict, human_decision=snapshot.human_decision,
                    synthetic=snapshot.synthetic, snapshot_sha256=_digest(snapshot),
                    records=[record.model_dump(mode="json") for record in records])
        # Check the real accepted sequence instead of inferring progress from a
        # successful HTTP/status return or a callback that returned invalid data.
        expected_roles = roles[:1] if action in {"start", "rereview"} else roles
        expected_states = tuple("reused" if context.parent_run_id and role == "A02" else "completed"
                                for role in expected_roles)
        reason = None
        if snapshot.error is not None:
            reason = "STAGE_FAILED"
            unknown = snapshot.error.outcome_unknown
            failed = next((r for r in reversed(records) if r.error is not None), None)
            error = failed.error if failed else snapshot.error
            item["error"] = {"code": error.code, "retryable": error.retryable,
                             "outcome_unknown": error.outcome_unknown}
        elif snapshot.context != context or snapshot.execution_status != expected_status:
            reason = "STATE_MISMATCH"
        elif snapshot.machine_verdict != expected_verdict:
            reason = "VERDICT_MISMATCH"
        elif snapshot.human_decision != expected_human:
            reason = "HUMAN_DECISION_MISMATCH"
        elif tuple(r.role for r in records) != expected_roles or tuple(r.status for r in records) != expected_states:
            reason = "STAGE_SEQUENCE_MISMATCH"
        elif context.parent_run_id and records[0].source_run_id != original.run_id:
            reason = "REUSE_SOURCE_MISMATCH"
        if action == "approve_changes":
            parent_snapshot = snapshot
        if context.parent_run_id and workflow.get(original.run_id) != parent_snapshot:
            reason = reason or "PARENT_CHANGED"
        item["status"] = "failed" if reason else "passed"
        if reason:
            item["reason"] = reason
            stopped = True
        actions.append(item)
    counts = {status: sum(a["status"] == status for a in actions)
              for status in ("passed", "failed", "skipped")}
    return {"contract_version": "2.0.0", "check": "configured_workflow", "case_id": case.id,
            "case_sha256": _digest(case), "configuration_sha256": _digest(configuration),
            "profile": configuration.profile, "storage": "memory", "passed": not stopped,
            "stopped_on_outcome_unknown": unknown,
            "configuration": describe_configuration(configuration),
            "summary": {"total": len(actions), **counts}, "actions": actions,
            "connection_verified": False, "production_ready": False,
            "scripted_approvals": True,
            "notice": "Explicit developer rehearsal with scripted approvals. Hashes cover canonical validated JSON. "
                      "Not authenticated human approval, model accuracy, production persistence or connectivity verification."}
