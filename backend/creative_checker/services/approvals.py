"""Product approvals operations."""

from copy import deepcopy

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.creative_checker import contracts as C
from backend.creative_checker.models import (
    Approval,
    Asset,
    AssetVersion,
    ChangeRequest,
    Job,
    Project,
    Run,
    User,
    new_id,
    utcnow,
)
from backend.creative_checker.services.common import fail
from backend.creative_checker.services.events import record_event
from backend.creative_checker.services.runs import approved_logos
from backend.creative_checker.services.serialization import change_json


def create_change_request(db: Session, project: Project, run: Run, user: User, body: C.CreateChangeRequest) -> ChangeRequest:
    if run.state not in {"waiting_review", "completed"}:
        fail(409, "review_not_finished", "A completed inspection is required before requesting changes")
    if db.scalar(select(Approval.id).where(Approval.run_id == run.id)):
        fail(409, "already_approved", "This run has already received final approval")
    ids = {item["id"] for item in run.snapshot["items"]}
    supplied = [str(item.criterion_id) for item in body.items]
    if len(set(supplied)) != len(supplied) or not set(supplied).issubset(ids):
        fail(422, "invalid_criteria", "Change requests must reference distinct criteria inspected in this run")
    change = ChangeRequest(id=new_id(), project_id=project.id, run_id=run.id,
                           items=[item.model_dump(mode="json") for item in body.items], message=body.message, created_by=user.id)
    db.add(change)
    db.flush()
    record_event(db, project, "change_request.created", user.id, {"change_request_id": change.id}, run.id)
    return change


def approve_change_request(db: Session, project: Project, change: ChangeRequest, user: User) -> ChangeRequest:
    if change.status == "approved":
        return change
    if change.status != "draft":
        fail(409, "invalid_state", "Change request is not awaiting approval")
    if db.scalar(select(Approval.id).where(Approval.run_id == change.run_id)):
        fail(409, "already_approved", "The source review has already received final approval")
    change.status, change.approved_by, change.approved_at = "approved", user.id, utcnow()
    db.add(Approval(project_id=project.id, target_type="change_request", target_id=change.id,
                    approved_by=user.id, snapshot=change_json(change)))
    run = db.get(Run, change.run_id)
    db.add(Job(kind="approval", project_id=project.id, target_id=change.run_id, attempt=run.attempt,
               payload={"decision": "changes_approved", "entity_id": change.id, "run_id": change.run_id, "attempt": run.attempt}))
    record_event(db, project, "change_request.approved", user.id, {"change_request_id": change.id}, change.run_id)
    return change


def approve_run(db: Session, project: Project, run: Run, user: User, body: C.FinalApproval) -> Approval:
    if db.scalar(select(Approval.id).where(Approval.run_id == run.id)):
        fail(409, "already_approved", "This run has already received final approval")
    if run.state not in {"waiting_review", "completed", "failed", "cancelled"}:
        fail(409, "review_not_finished", "Active executions cannot receive final approval")
    version = db.get(AssetVersion, run.asset_version_id)
    latest = db.scalar(select(func.max(AssetVersion.version)).where(AssetVersion.asset_id == version.asset_id))
    if version.version != latest or run.criteria_version_id != project.current_criteria_id or run.snapshot["document_id"] != project.current_document_id:
        fail(409, "stale_review", "The latest asset and current approved criteria must be inspected before final approval")
    if [(x["id"], x["file_id"]) for x in run.snapshot["logos"]] != [(x["id"], x["file_id"]) for x in approved_logos(db, project.id)]:
        fail(409, "stale_logos", "Approved reference logos changed; inspect the asset again")
    pending_change = db.scalar(select(ChangeRequest.id).where(ChangeRequest.run_id == run.id,
                                                              ChangeRequest.status.in_(["draft", "approved"])))
    if pending_change:
        fail(409, "changes_requested", "Submit a new asset version and complete reinspection before final approval")
    applicable = {item["id"]: item for item in run.snapshot["items"]}
    results = {result.get("criterion_id"): result for result in run.results}
    if len(results) != len(run.results) or not set(results).issubset(applicable):
        fail(409, "invalid_results", "Inspection results contain duplicate or unrelated criterion IDs")
    exceptions = {str(item_id) for item_id in body.exception_criterion_ids}
    if len(exceptions) != len(body.exception_criterion_ids) or not exceptions.issubset(applicable):
        fail(422, "invalid_exception", "Exceptions must reference distinct criteria in this inspection")
    def passed(key):
        result = results.get(key, {})
        return result.get("execution_status") == "completed" and result.get("verdict") == "pass"

    needed = {key for key, item in applicable.items() if item.get("required", True) and not passed(key)}
    for group in run.snapshot.get("groups", []):
        group_ids = set(group.get("criterion_ids", [])) & applicable.keys()
        if group.get("condition") or group.get("exception"):
            needed.update(group_ids)
        elif group.get("operator") == "any" and any(passed(key) for key in group_ids):
            needed.difference_update(group_ids)
    if run.verdict != "pass" and not needed:
        needed = {key for key in applicable if not passed(key)}
    reason = body.exception_reason.strip() if body.exception_reason else None
    if not needed.issubset(exceptions) or ((exceptions or run.state in {"failed", "cancelled"} or run.verdict != "pass") and not reason):
        fail(409, "exception_required", "Non-passing required criteria and unresolved groups require explicit exception targets and a reason")
    approval = Approval(id=new_id(), project_id=project.id, run_id=run.id, target_type="run", target_id=run.id,
                        approved_by=user.id, exception_reason=reason,
                        exception_criterion_ids=sorted(exceptions), original_verdict=run.verdict,
                        snapshot={"input": deepcopy(run.snapshot), "results": deepcopy(run.results),
                                  "report": deepcopy(run.report), "state": run.state, "attempt": run.attempt,
                                  "errors": deepcopy(run.errors), "verdict": run.verdict})
    db.add(approval)
    db.add(Job(kind="approval", project_id=project.id, target_id=run.id, attempt=run.attempt,
               payload={"decision": "final_approved", "entity_id": approval.id, "run_id": run.id, "attempt": run.attempt}))
    db.get(Asset, version.asset_id).needs_review = False
    record_event(db, project, "run.approved", user.id, {"approval_id": approval.id,
                 "exception_criterion_ids": sorted(exceptions), "original_verdict": run.verdict}, run.id)
    # Human approval never rewrites the execution state or machine verdict.
    return approval
