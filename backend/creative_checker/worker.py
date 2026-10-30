"""Lease-based CPU worker; a quantum is one video batch or one human decision."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import timedelta
import logging
import threading
import time

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.engine import make_url

from .database import create_engine, session_factory
from .graph import GraphStore
from .models import Document, Job, Project, Run, new_id, utcnow
from .orchestration import build_graph
from .projection import deliver_one
from .services import record_event
from .settings import get_settings
from .tools import ToolFailure

logger = logging.getLogger(__name__)
LEASE_SECONDS = 900


def checkpoint_dsn(database_url):
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


def thread_id(job):
    kind = "document" if job.kind == "document" or job.payload.get("decision") == "criteria_approved" else "review"
    return f"{job.project_id}:{kind}:{job.target_id}:{job.attempt}"


def claim(sessions):
    with sessions.begin() as db:
        eligible = or_(
            and_(Job.state == "queued", Job.available_at <= utcnow()),
            and_(Job.state.in_(["running", "cancel_requested"]), Job.lease_until < utcnow()),
        )
        candidate = db.scalar(select(Job).join(Project, Project.id == Job.project_id)
            .where(eligible, Project.archived.is_(False)).order_by(Job.available_at, Job.created_at).limit(1))
        if candidate is None:
            return None
        # Match the API/purge lock order: project, then job, then mutable target.
        project = db.scalar(select(Project).where(Project.id == candidate.project_id, Project.archived.is_(False))
                            .with_for_update(skip_locked=True))
        if project is None:
            return None
        job = db.scalar(select(Job).where(Job.id == candidate.id, eligible).with_for_update(skip_locked=True))
        if job is None:
            return None
        if job.state == "cancel_requested":
            job.state, job.lease_token, job.lease_until = "cancelled", None, None
            run = db.get(Run, job.target_id)
            if run:
                run.state, run.stage = "cancelled", "cancelled"
            return None
        job.state, job.lease_token = "running", new_id()
        job.lease_until = utcnow() + timedelta(seconds=LEASE_SECONDS)
        if job.kind == "review":
            run = db.get(Run, job.target_id)
            if run.state == "queued":
                run.state, run.stage = "running", "resume" if job.payload.get("yielded") else "prepare"
        elif job.kind == "document":
            document = db.get(Document, job.target_id)
            if document.status == "queued":
                document.status = "running"
        return job


@contextmanager
def renew_lease(sessions, job):
    stop = threading.Event()

    def renew():
        while not stop.wait(20):
            try:
                with sessions.begin() as db:
                    db.execute(update(Job).where(Job.id == job.id, Job.lease_token == job.lease_token,
                        Job.state.in_(["running", "cancel_requested"])).values(lease_until=utcnow() + timedelta(seconds=LEASE_SECONDS)))
            except Exception:
                logger.error("Lease renewal failed; job=%s", job.id)

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def finish(sessions, job, *, yielded=False, error=None):
    with sessions.begin() as db:
        project = db.scalar(select(Project).where(Project.id == job.project_id).with_for_update())
        if project is None:
            return
        current = db.scalar(select(Job).where(Job.id == job.id, Job.lease_token == job.lease_token).with_for_update())
        if current is None:
            return
        cancelled = current.state == "cancel_requested" or (error and error["code"] == "cancelled")
        current.state = "cancelled" if cancelled else "queued" if yielded else "failed" if error else "completed"
        current.lease_token = current.lease_until = None
        current.error = error
        if yielded:
            current.payload = {**current.payload, "yielded": True}
            current.available_at = utcnow() + timedelta(seconds=1)
        if current.kind == "review":
            run = db.get(Run, current.target_id)
            if cancelled:
                run.state, run.stage = "cancelled", "cancelled"
            elif error:
                run.state, run.stage = "failed", "failed"
                run.errors = [*run.errors, error]
            elif yielded:
                run.state, run.stage = "queued", "awaiting_gpu"
        elif current.kind == "document" and error:
            document = db.get(Document, current.target_id)
            document.status, document.errors = "failed", [*document.errors, error]
        record_event(db, project, "job.yielded" if yielded else "job.finished", None,
                     {"job_id": current.id, "state": current.state, "error": error}, current.target_id if current.kind == "review" else None)


def process(settings, sessions, checkpointer, job):
    graph = build_graph(settings, sessions, checkpointer, lease_token=job.lease_token)
    config = {"configurable": {"thread_id": thread_id(job)}, "recursion_limit": 64}
    state = graph.get_state(config)
    pending = [interrupt for task in state.tasks for interrupt in task.interrupts]
    if job.kind == "approval":
        with sessions() as db:
            target = db.get(Run, job.target_id) if job.payload.get("run_id") else None
            exception = bool(target and target.state in {"failed", "cancelled"})
        if exception:
            # A failed graph cannot be resumed through its model node to record a human exception.
            exception_config = {"configurable": {"thread_id": thread_id(job) + ":exception:" + job.payload["entity_id"]}}
            saved = graph.get_state(exception_config)
            if not saved.values:
                graph.invoke({"job_id": job.id, "kind": "exception", "target_id": job.target_id, "attempt": job.attempt}, exception_config)
            if graph.get_state(exception_config).next:
                graph.invoke(Command(resume=job.payload), exception_config)
            finish(sessions, job)
            return
        if pending:
            expected = "criteria_approval" if job.payload["decision"] == "criteria_approved" else "review_approval"
            if pending[0].value.get("kind") != expected:
                finish(sessions, job, yielded=True)
                return
            graph.invoke(Command(resume=job.payload), config)
        elif state.next or not state.values:
            # An approval can commit just before the original job checkpoints its interrupt.
            finish(sessions, job, yielded=True)
            return
        finish(sessions, job)
        return
    if pending and pending[0].value.get("kind") != "queue_yield":
        finish(sessions, job)
        return
    initial = {"job_id": job.id, "kind": job.kind, "target_id": job.target_id, "attempt": job.attempt}
    result = graph.invoke(Command(resume={"decision": "continue"}) if pending else None if state.values else initial, config)
    interrupts = result.get("__interrupt__", [])
    yielded = bool(interrupts and interrupts[0].value.get("kind") == "queue_yield")
    finish(sessions, job, yielded=yielded)


def run(once=False, projection_only=False):
    settings = get_settings()
    if settings.testing:
        raise RuntimeError("The worker requires PostgreSQL; testing mode is not a queue backend")
    engine = create_engine(settings.database_url)
    sessions = session_factory(engine)
    graph_store = GraphStore(settings)
    try:
        with PostgresSaver.from_conn_string(checkpoint_dsn(settings.database_url)) as checkpointer:
            while True:
                worked = False
                if not projection_only:
                    job = claim(sessions)
                    if job:
                        worked = True
                        try:
                            with renew_lease(sessions, job):
                                process(settings, sessions, checkpointer, job)
                        except Exception as exc:
                            safe = isinstance(exc, ToolFailure)
                            error = {"code": exc.code if safe else "worker_failed",
                                     "message": str(exc)[:500] if safe else "작업 처리에 실패했습니다. 실행 기록을 확인해 주세요."}
                            if error["code"] == "gpu_busy":
                                finish(sessions, job, yielded=True)
                            else:
                                finish(sessions, job, error=error)
                            logger.error("Job failed; job=%s code=%s", job.id, error["code"])
                if projection_only:
                    worked = deliver_one(sessions, graph_store)
                if once:
                    return
                if not worked:
                    time.sleep(1)
    finally:
        graph_store.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--projection-only", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    run(args.once, args.projection_only)
