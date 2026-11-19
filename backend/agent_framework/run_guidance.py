"""On-demand A01 guidance and A08 comparison over authorized, stored runs."""
from contextlib import contextmanager
from time import monotonic
from urllib.parse import urlsplit

from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError

from .agents import OpenAICompatibleChat, RereviewAgent, RunCoordinator
from .contracts import RunSnapshot
from .mcp_server import McpToolClient, READ_TOOL_NAMES
from .model_route import OPENAI_BASE_URL, openai_settings, route_of
from .ports import AdapterFailure


GUIDANCE_STATES = frozenset({'queued', 'failed', 'outcome_unknown'})
COMPARISON_STATES = frozenset({'waiting_review', 'waiting_revision', 'completed'})
GUIDANCE_TIMEOUT = 22


class RunGuidance:
    def __init__(self, values):
        self.values = dict(values)
        self.a01_enabled = values.get('CC_A01_ENABLED') == '1'
        self.a08_enabled = values.get('CC_A08_ENABLED') == '1'

    @classmethod
    def configured(cls, values):
        if ('1' not in (values.get('CC_A01_ENABLED'), values.get('CC_A08_ENABLED'))
                or len(values.get('CC_MCP_TOKEN', '')) < 32):
            return None
        try:
            endpoint = urlsplit(values.get('CC_MCP_URL', ''))
            if (endpoint.scheme != 'http' or endpoint.hostname not in ('127.0.0.1', '::1')
                    or endpoint.username is not None or endpoint.password is not None
                    or not endpoint.port or endpoint.path != '/mcp' or endpoint.query or endpoint.fragment):
                return None
        except ValueError:
            return None
        return cls(values)

    def _chat(self, route, deadline):
        if route == 'openai':
            settings = openai_settings(self.values)
            return OpenAICompatibleChat(OPENAI_BASE_URL, settings.api_key, model=settings.model,
                                        reasoning_effort=settings.reasoning_effort, deadline=deadline)
        key = self.values.get('CC_QWEN_API_KEY') or self.values.get('A02_QWEN_API_KEY')
        return OpenAICompatibleChat(self.values.get('CC_QWEN_BASE_URL', ''), key, deadline=deadline)

    @contextmanager
    def _agent_services(self, route, *run_ids):
        deadline = monotonic() + GUIDANCE_TIMEOUT
        tools = _GuidanceTools(self.values['CC_MCP_URL'], self.values['CC_MCP_TOKEN'], deadline, run_ids)
        model = self._chat(route, deadline)
        try:
            yield model, tools
        finally:
            model.close()

    def assess(self, store, subject, run_id):
        with unit_of_work(store.engine) as connection:
            snapshot, route = _authorized_run(store, connection, subject, run_id)
        if snapshot.execution_status not in GUIDANCE_STATES or getattr(snapshot.error, 'code', None) == 'USER_CANCELLED':
            raise AuthError('A01_GUIDANCE_STATE_CHANGED', 409)

        try:
            with self._agent_services(route, run_id) as (model, tools):
                note = RunCoordinator(model, tools).assess(run_id)
        except AdapterFailure as error:
            if error.code == 'FRAMEWORK_ACCESS_REVOKED':
                raise AuthError('PROJECT_ACCESS_DENIED', 403) from None
            if error.code == 'AGENT_ACTION_NOT_ALLOWED':
                raise AuthError('A01_GUIDANCE_STATE_CHANGED', 409) from None
            raise AuthError('A01_GUIDANCE_UNAVAILABLE', 503) from None

        current = store.get(subject, run_id)
        if (current.execution_status != snapshot.execution_status or current.execution_status != note.execution_status
                or current.error != snapshot.error):
            raise AuthError('A01_GUIDANCE_STATE_CHANGED', 409)
        return note

    def compare(self, store, subject, child_id):
        with unit_of_work(store.engine) as connection:
            child, route = _authorized_run(store, connection, subject, child_id)
            parent_id = child.context.parent_run_id
            if (not parent_id or not child.baseline or not child.validated or not child.validated.items
                    or child.execution_status not in COMPARISON_STATES):
                raise AuthError('A08_COMPARISON_NOT_READY', 409)
            parent, parent_route = _authorized_run(store, connection, subject, parent_id)
        if (parent.context.project_id != child.context.project_id or parent.baseline != child.baseline
                or parent_route != route or not parent.validated or not parent.validated.items):
            raise AuthError('A08_COMPARISON_NOT_READY', 409)

        try:
            with self._agent_services(route, parent_id, child_id) as (model, tools):
                summary = RereviewAgent(model, tools).compare(parent_id, child_id)
        except AdapterFailure as error:
            if error.code == 'FRAMEWORK_ACCESS_REVOKED':
                raise AuthError('PROJECT_ACCESS_DENIED', 403) from None
            if error.code == 'RUN_COMPARISON_CHANGED':
                raise AuthError('A08_COMPARISON_NOT_READY', 409) from None
            raise AuthError('A08_COMPARISON_UNAVAILABLE', 503) from None

        current_parent = store.get(subject, parent_id)
        current_child = store.get(subject, child_id)
        if (not summary.compared or current_parent.validated != parent.validated
                or current_child.validated != child.validated or current_child.execution_status != summary.child_status):
            raise AuthError('A08_COMPARISON_NOT_READY', 409)
        return summary


def _authorized_run(store, connection, subject, run_id):
    row = store._row(connection, run_id)
    snapshot = RunSnapshot.model_validate(row['snapshot_json'])
    store._access(connection, subject, snapshot.context, write=False)
    return snapshot, route_of(row['input_json'])


class _GuidanceTools:
    def __init__(self, url, token, deadline, run_ids):
        self.url, self.token, self.deadline, self.run_ids = url, token, deadline, run_ids

    def call(self, name, arguments):
        if name not in READ_TOOL_NAMES or arguments.get('run_id') not in self.run_ids:
            raise AdapterFailure('RUN_SCOPE_MISMATCH', 'The tool request is outside the authorized comparison or guidance.')
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise AdapterFailure('TOOL_TIMEOUT', 'The guidance request reached its time limit.')
        receipt = McpToolClient(self.url, self.token, timeout=min(remaining, 4)).call(name, arguments)
        if receipt.get('state') != 'completed':
            return receipt
        # Keep status, verdict and decision facts for guidance and comparisons.
        result = receipt['result']
        if name == 'get_run':
            keys = ('run_id', 'parent_run_id', 'execution_status', 'machine_verdict', 'human_decision', 'validated_items',
                    'synthetic')
            result = {**{key: result[key] for key in keys},
                      'error': {'code': result['error']['code']} if result['error'] else None}
        elif name == 'get_steps':
            result = {'execution_status': result['execution_status'],
                      'records': [{key: item[key] for key in ('role', 'status')} for item in result['records']]}
        elif name == 'get_approval_history':
            result = [{'decision': item['decision']} for item in result]
        return {**receipt, 'result': result}
