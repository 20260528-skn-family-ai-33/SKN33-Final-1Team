"""PostgreSQL backend for the framework MCP tools; each call acts as a run requester.

An MCP call carries the server's Bearer token, not a login session. Like the
durable worker, every call rechecks the requester's current account, active
owner/editor membership and active project (PostgresStore.worker_access)
instead of borrowing or extending a session. The token holder can name any
requester, so the token must stay on the backend host. Runs created through
MCP carry origin='mcp' on their run.created event. Approvals stay on HTTP.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from uuid import UUID

from sqlalchemy import select

from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from .contracts import AssetRef, Context, DocumentRef, RunSnapshot, StepTimeline
from .mcp_server import ToolRefusal
from .postgres import PostgresStore, runs

# Same tool code as the in-memory tools where the meaning is the same.
CODES = {'FRAMEWORK_RUN_NOT_FOUND': 'RUN_NOT_FOUND'}
MESSAGES = {
    'RUN_NOT_FOUND': 'The run does not exist in this store.',
    'FRAMEWORK_ACCESS_REVOKED': 'The requester has no active owner/editor access to the project.',
    'FRAMEWORK_ACTOR_MISMATCH': 'Only the parent run requester can request its rereview.',
    'FRAMEWORK_UUID_REQUIRED': 'Project and actor IDs must be UUIDs.',
    'FRAMEWORK_IDEMPOTENCY_CONFLICT': 'This run ID already holds a different input.',
    'FRAMEWORK_REVISION_CONFLICT': 'This parent already has a rereview with a different input.',
    # Registered poster runs pin a baseline; their commands stay on the HTTP API.
    'USE_POSTER_REVIEW_ENDPOINT': 'Registered posters start through the poster review command, not start_run.',
    'USE_POSTER_REVISION_ENDPOINT': 'A poster review run is revised through the poster revision command, not request_rereview.',
    # Evidence tools (mcp_evidence.py): read-only, so every refusal leaves the store unchanged.
    'CRITERIA_NOT_APPROVED': 'The criteria run has no approved criteria version yet.',
    'CRITERIA_REVIEW_REQUIRES_PDF_ONLY': 'This run has no criteria review record; ask with its document run or poster review run.',
    'CRITERIA_DRAFT_NOT_READY': 'The A02 criteria draft of this run is not ready.',
    'CRITERIA_RECORD_INTEGRITY_INVALID': 'Stored criteria or approval records failed their integrity check.',
    'POSTER_BASELINE_MISMATCH': 'The poster review run does not match its pinned approved criteria.',
}


@dataclass(frozen=True, slots=True)
class RunActor:
    """Stand-in subject: the requester's user ID only, never a session."""
    user_id: str


class ActorAccess:
    """PostgresStore authorization: the worker rule instead of a session check."""

    def __init__(self, recheck):
        self.recheck = recheck

    def project(self, connection, subject, project_id, *, action='read'):
        # Reads need owner/editor too: every tool call acts for a run requester.
        self.recheck(connection, project_id, UUID(subject.user_id))


class McpStore(PostgresStore):
    """PostgresStore for the MCP process; must load the same team config as the worker."""

    def __init__(self, engine, configuration):
        super().__init__(engine, configuration, ActorAccess(self.worker_access))

    def event(self, c, run_id, kind, **metadata):
        if kind == 'run.created':
            metadata['origin'] = 'mcp'
        super().event(c, run_id, kind, **metadata)


def _refusals(method):
    """Store refusals are rolled back before they reach here, so nothing was applied."""
    @wraps(method)
    def call(self, *args):
        try:
            return method(self, *args)
        except AuthError as error:
            code = CODES.get(error.code, error.code)
            raise ToolRefusal(code, MESSAGES.get(code, 'The store refused the request.')) from None
    return call


class PostgresRuns:
    """RunBackend over a PostgresStore; other errors propagate as unknown outcomes."""

    def __init__(self, store: PostgresStore):
        self.store = store

    def requester(self, run_id: str) -> RunActor:
        # A run's requester never changes, so the store call can recheck it afterwards.
        with unit_of_work(self.store.engine) as c:
            actor = c.execute(select(runs.c.actor_id).where(runs.c.run_id == run_id)).scalar_one_or_none()
        if actor is None:
            raise AuthError('FRAMEWORK_RUN_NOT_FOUND', 404)
        return RunActor(str(actor))

    @_refusals
    def get(self, run_id: str) -> RunSnapshot:
        return self.store.get(self.requester(run_id), run_id)

    @_refusals
    def get_steps(self, run_id: str) -> StepTimeline:
        return self.store.get_steps(self.requester(run_id), run_id)

    @_refusals
    def start(self, context: Context, document: DocumentRef, asset: AssetRef) -> RunSnapshot:
        return self.store.start(RunActor(context.actor_id), context, document, asset)

    @_refusals
    def rereview(self, parent_run_id: str, context: Context, asset: AssetRef) -> RunSnapshot:
        if self.requester(parent_run_id).user_id != context.actor_id:
            raise AuthError('FRAMEWORK_ACTOR_MISMATCH', 403)
        return self.store.rereview(RunActor(context.actor_id), parent_run_id, context, asset)
