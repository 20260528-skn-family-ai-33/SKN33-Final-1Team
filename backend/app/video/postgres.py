"""Append-only approvals for project-linked videos in the common framework database."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
from uuid import UUID

from sqlalchemy import text

from backend.agent_framework.graph_projection import mark_project_changed
from backend.agent_framework.postgres_schema import check_app_permissions, check_extension, video_approvals_installed
from backend.app.persistence.database import PersistenceError
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from backend.app.services.business import safe_transaction
from backend.app.video.workflow import DECISION_STATES, VideoDecision, record_decision

_INSERT = text("""
    INSERT INTO agent_framework.video_approvals
      (run_id, project_id, submitted_by, root_run_id, parent_run_id, asset_version, criteria_version,
       criteria_sha256, asset_sha256, command_id, actor_id, decision, reason, human_checks, approved_at)
    VALUES
      (:run_id, :project_id, :submitted_by, :root_run_id, :parent_run_id, :asset_version, :criteria_version,
       :criteria_sha256, :asset_sha256, :command_id, :actor_id, :decision, :reason, CAST(:human_checks AS jsonb), :approved_at)
    ON CONFLICT (run_id) DO NOTHING RETURNING run_id
""")
_SELECT = "SELECT * FROM agent_framework.video_approvals"
logger = logging.getLogger(__name__)


def _binding(job):
    workflow = job["workflow"]
    return {
        "run_id": UUID(job["id"]),
        "project_id": UUID(workflow["project_id"]),
        "submitted_by": UUID(workflow["criteria_approved_by"]),
        "root_run_id": UUID(workflow["root_run_id"]),
        "parent_run_id": UUID(workflow["parent_run_id"]) if workflow["parent_run_id"] else None,
        "asset_version": workflow["asset_version"],
        "criteria_version": job["criteria_version"],
        "criteria_sha256": workflow["criteria_sha256"],
        "asset_sha256": job["media"]["sha256"],
    }


def _saved_values(job):
    values = _binding(job)
    saved, = job["workflow"]["decisions"]
    command = VideoDecision.model_validate({key: saved[key] for key in VideoDecision.model_fields})
    approved_at = datetime.fromisoformat(saved["approved_at"])
    if (job["status"] != "completed" or approved_at.utcoffset() is None
            or command.asset_sha256 != values["asset_sha256"] or command.criteria_version != values["criteria_version"]
            or job["workflow"]["human_decision"] != DECISION_STATES[command.decision]):
        raise ValueError("Invalid saved video approval")
    return {**values, **command.model_dump(), "actor_id": UUID(saved["actor_id"]),
            "human_checks": json.dumps(command.human_checks), "approved_at": approved_at}


def _apply(job, saved):
    workflow = job["workflow"]
    if saved is None:
        if workflow["decisions"] or workflow["human_decision"] != "unreviewed":
            raise AuthError("VIDEO_APPROVAL_IMPORT_REQUIRED", 503)
        return
    if any(saved[key] != value for key, value in _binding(job).items()):
        raise AuthError("VIDEO_REVIEW_VERSION_CHANGED", 409)
    payload = VideoDecision.model_validate({key: saved[key] for key in VideoDecision.model_fields}).model_dump(mode="json")
    workflow["decisions"] = [{**payload, "actor_id": str(saved["actor_id"]), "approved_at": saved["approved_at"].isoformat()}]
    workflow["human_decision"] = DECISION_STATES[payload["decision"]]


def _lock(connection, run_id):
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                       {"key": f"video-approval:{run_id}"})


def _append(connection, values):
    if connection.execute(_INSERT, values).scalar_one_or_none() is not None:
        mark_project_changed(connection, values["project_id"], "video.approval_recorded", values["run_id"])


class VideoApprovals:
    def __init__(self, engine, authorization):
        self.engine = engine
        self.authorization = authorization

    @classmethod
    def open(cls, engine, authorization):
        with unit_of_work(engine) as connection:
            if not video_approvals_installed(connection):
                return None
            check_extension(connection)
            check_app_permissions(connection)
        return cls(engine, authorization)

    @safe_transaction
    def attach(self, jobs):
        linked = {UUID(job["id"]): job for job in jobs if job.get("workflow")}
        if not linked:
            return
        with unit_of_work(self.engine) as connection:
            rows = connection.execute(text(_SELECT + " WHERE run_id = ANY(CAST(:ids AS uuid[]))"),
                                      {"ids": list(linked)}).mappings()
            saved = {row["run_id"]: row for row in rows}
        for run_id, job in linked.items():
            _apply(job, saved.get(run_id))

    @safe_transaction
    def approve(self, job, command, identity):
        subject, developer = identity
        job = {**job, "workflow": deepcopy(job["workflow"])}
        binding = _binding(job)
        with unit_of_work(self.engine) as connection:
            self.authorization.project(connection, subject, binding["project_id"], action="write", developer_allowed=developer)
            _lock(connection, binding["run_id"])
            row = connection.execute(text(_SELECT + " WHERE run_id = :id"),
                                     {"id": binding["run_id"]}).mappings().one_or_none()
            _apply(job, row)
            if record_decision(job, command, str(subject.user_id), datetime.now(timezone.utc).isoformat()):
                _append(connection, _saved_values(job))
        return job

    def import_history(self, root, *, isolate_users):
        invalid = 0
        for path in root.glob("*/*/result.json" if isolate_users else "*/result.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                workflow = job.get("workflow") if isinstance(job, dict) else None
                if not isinstance(workflow, dict) or not workflow.get("decisions"):
                    continue
                values = _saved_values(job)
                if str(values["run_id"]) != path.parent.name or (
                    isolate_users and str(values["submitted_by"]) != path.parent.parent.name
                ):
                    raise ValueError("Video approval storage scope mismatch")
                with unit_of_work(self.engine) as connection:
                    _lock(connection, values["run_id"])
                    _append(connection, values)
            except (OSError, ValueError, TypeError, KeyError):
                invalid += 1
            except PersistenceError as error:
                if error.code != "DB_CONSTRAINT_VIOLATION":
                    raise
                invalid += 1
        if invalid:
            logger.warning("video_approval_history_import_skipped", extra={"count": invalid})
