"""A01 guidance for an authorized run, requested separately from status polling."""
from time import monotonic
from urllib.parse import urlsplit

from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError

from .agents import OpenAICompatibleChat, RunCoordinator
from .contracts import RunSnapshot
from .mcp_server import McpToolClient
from .model_route import OPENAI_BASE_URL, openai_settings, route_of
from .ports import AdapterFailure


GUIDANCE_STATES = frozenset({'queued', 'failed', 'outcome_unknown'})
GUIDANCE_TIMEOUT = 22


class RunGuidance:
    def __init__(self, values):
        self.values = dict(values)

    @classmethod
    def configured(cls, values):
        if values.get('CC_A01_ENABLED') != '1' or len(values.get('CC_MCP_TOKEN', '')) < 32:
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

    def assess(self, store, subject, run_id):
        with unit_of_work(store.engine) as connection:
            row = store._row(connection, run_id)
            snapshot = RunSnapshot.model_validate(row['snapshot_json'])
            store._access(connection, subject, snapshot.context, write=False)
            route = route_of(row['input_json'])
        if snapshot.execution_status not in GUIDANCE_STATES or getattr(snapshot.error, 'code', None) == 'USER_CANCELLED':
            raise AuthError('A01_GUIDANCE_STATE_CHANGED', 409)

        deadline = monotonic() + GUIDANCE_TIMEOUT
        tools = _GuidanceTools(self.values['CC_MCP_URL'], self.values['CC_MCP_TOKEN'], deadline)
        try:
            model = self._chat(route, deadline)
            try:
                note = RunCoordinator(model, tools).assess(run_id)
            finally:
                model.close()
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


class _GuidanceTools:
    def __init__(self, url, token, deadline):
        self.url, self.token, self.deadline = url, token, deadline

    def call(self, name, arguments):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise AdapterFailure('TOOL_TIMEOUT', 'The guidance request reached its time limit.')
        receipt = McpToolClient(self.url, self.token, timeout=min(remaining, 4)).call(name, arguments)
        if receipt.get('state') != 'completed':
            return receipt
        # Run guidance needs status facts, step names and decision types.
        result = receipt['result']
        if name == 'get_run':
            keys = ('run_id', 'execution_status', 'machine_verdict', 'human_decision', 'validated_items', 'synthetic')
            result = {**{key: result[key] for key in keys},
                      'error': {'code': result['error']['code']} if result['error'] else None}
        elif name == 'get_steps':
            result = {'execution_status': result['execution_status'],
                      'records': [{key: item[key] for key in ('role', 'status')} for item in result['records']]}
        elif name == 'get_approval_history':
            result = [{'decision': item['decision']} for item in result]
        return {**receipt, 'result': result}
