"""MCP tools over the PostgreSQL backend without a database: requester and refusal rules."""
from uuid import UUID

import pytest

from backend.agent_framework.adapters.synthetic import make_agents, sample_inputs
from backend.agent_framework.composition import load_config
from backend.agent_framework.mcp_postgres import ActorAccess, McpStore, PostgresRuns, RunActor
from backend.agent_framework.mcp_server import FrameworkTools
from backend.agent_framework.runtime import Workflow
from backend.app.persistence.database import PersistenceError
from backend.app.security.errors import AuthError

PROJECT = UUID('00000000-0000-4000-8000-000000000001')
REQUESTER = '00000000-0000-4000-8000-0000000000a1'


class FakeStore:
    """Records the subject of each call; refuses or fails when told to."""

    def __init__(self, error=None):
        self.workflow = Workflow(make_agents())
        context, document, asset = sample_inputs()
        self.snapshot = self.workflow.start(context, document, asset)
        self.calls, self.error = [], error

    def _record(self, name, subject):
        self.calls.append((name, subject))
        if self.error:
            raise self.error

    def get(self, subject, run_id):
        self._record('get', subject)
        return self.workflow.get(run_id)

    def get_steps(self, subject, run_id):
        self._record('get_steps', subject)
        return self.workflow.get_steps(run_id)

    def start(self, subject, context, document, asset):
        self._record('start', subject)
        return self.snapshot

    def rereview(self, subject, parent_run_id, context, asset):
        self._record('rereview', subject)
        return self.snapshot


class Runs(PostgresRuns):
    """Requester lookup from a dict; the SQL lookup is covered by db_integration."""
    owners = {'demo-run-1': 'synthetic-reviewer'}

    def requester(self, run_id):
        if run_id not in self.owners:
            raise AuthError('FRAMEWORK_RUN_NOT_FOUND', 404)
        return RunActor(self.owners[run_id])


def json_inputs(*args):
    return [value.model_dump(mode='json') for value in sample_inputs(*args)]


def test_actor_access_applies_the_worker_rule_to_reads_and_writes():
    seen = []
    access = ActorAccess(lambda connection, project_id, actor_id: seen.append((connection, project_id, actor_id)))
    access.project('c', RunActor(REQUESTER), PROJECT, action='read')
    access.project('c', RunActor(REQUESTER), PROJECT, action='write')
    assert seen == [('c', PROJECT, UUID(REQUESTER))] * 2


def test_reads_act_as_the_run_requester():
    store = FakeStore()
    tools = FrameworkTools(Runs(store))
    assert tools.call('get_run', {'run_id': 'demo-run-1'})['state'] == 'completed'
    assert tools.call('get_steps', {'run_id': 'demo-run-1'})['state'] == 'completed'
    assert tools.call('get_approval_history', {'run_id': 'demo-run-1'}) == {'state': 'completed', 'result': []}
    assert store.calls == [('get', RunActor('synthetic-reviewer')), ('get_steps', RunActor('synthetic-reviewer')),
                           ('get', RunActor('synthetic-reviewer'))]


def test_start_acts_as_the_named_requester():
    store = FakeStore()
    context, document, asset = json_inputs('demo-run-9')
    receipt = FrameworkTools(Runs(store)).call('start_run', {'context': context, 'document': document, 'asset': asset})
    assert receipt['state'] == 'completed' and store.calls == [('start', RunActor('synthetic-reviewer'))]


def test_rereview_is_refused_for_anyone_but_the_parent_requester():
    store = FakeStore()
    tools = FrameworkTools(Runs(store))
    child, _, asset = json_inputs('demo-run-2', 'poster-v2', 'demo-run-1')
    other = {**child, 'actor_id': 'someone-else'}
    refused = tools.call('request_rereview', {'parent_run_id': 'demo-run-1', 'context': other, 'asset': asset})
    assert refused['error']['code'] == 'FRAMEWORK_ACTOR_MISMATCH' and store.calls == []
    allowed = tools.call('request_rereview', {'parent_run_id': 'demo-run-1', 'context': child, 'asset': asset})
    assert allowed['state'] == 'completed' and store.calls == [('rereview', RunActor('synthetic-reviewer'))]


def test_store_refusals_keep_their_code():
    assert FrameworkTools(Runs(FakeStore())).call('get_run', {'run_id': 'missing'})['error']['code'] == 'RUN_NOT_FOUND'
    revoked = FrameworkTools(Runs(FakeStore(AuthError('FRAMEWORK_ACCESS_REVOKED', 403))))
    assert revoked.call('get_run', {'run_id': 'demo-run-1'}) == {'state': 'failed', 'error': {
        'code': 'FRAMEWORK_ACCESS_REVOKED', 'message': 'The requester has no active owner/editor access to the project.'}}


def test_poster_review_refusals_point_to_the_http_commands():
    context, document, asset = json_inputs('demo-run-9')
    started = FrameworkTools(Runs(FakeStore(AuthError('USE_POSTER_REVIEW_ENDPOINT', 409)))).call(
        'start_run', {'context': context, 'document': document, 'asset': asset})
    assert started['error'] == {'code': 'USE_POSTER_REVIEW_ENDPOINT',
                                'message': 'Registered posters start through the poster review command, not start_run.'}
    child, _, asset = json_inputs('demo-run-2', 'poster-v2', 'demo-run-1')
    revised = FrameworkTools(Runs(FakeStore(AuthError('USE_POSTER_REVISION_ENDPOINT', 409)))).call(
        'request_rereview', {'parent_run_id': 'demo-run-1', 'context': child, 'asset': asset})
    assert revised['error']['code'] == 'USE_POSTER_REVISION_ENDPOINT' and 'poster revision command' in revised['error']['message']


def test_database_errors_are_not_turned_into_refusals():
    # A lost commit acknowledgement may hide an applied write: the caller must see an exception.
    store = FakeStore(PersistenceError('DB_OPERATION_FAILED', 'The database operation failed.'))
    context, document, asset = json_inputs('demo-run-9')
    with pytest.raises(PersistenceError):
        FrameworkTools(Runs(store)).call('start_run', {'context': context, 'document': document, 'asset': asset})


def test_mcp_store_rechecks_with_worker_access_and_marks_created_runs(monkeypatch):
    from backend.agent_framework import graph_projection
    store = McpStore(None, load_config('examples/agent_framework/team_config.synthetic.json'))
    assert store.authorization.recheck == store.worker_access
    changed = []
    monkeypatch.setattr(graph_projection, 'mark_project_changed', lambda c, pid, kind, target: changed.append(kind))

    class Connection:
        statements = []

        def execute(self, statement):
            params = statement.compile().params
            if 'metadata_json' in params:
                self.statements.append(params['metadata_json'])

            class Row:
                scalar_one = staticmethod(lambda: 'project-1')
            return Row()

    store.event(Connection(), 'run-1', 'run.created', parent_run_id=None)
    store.event(Connection(), 'run-1', 'job.queued', phase='extract')
    assert Connection.statements == [{'parent_run_id': None, 'origin': 'mcp'}, {'phase': 'extract'}]
    assert changed == ['run.created']  # The team's projector also hears about runs created through MCP.
    assert not hasattr(PostgresRuns, 'approve')
