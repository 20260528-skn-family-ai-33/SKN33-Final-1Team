from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.agent_framework import postgres, run_guidance
from backend.agent_framework.adapters.synthetic import make_agents, sample_inputs
from backend.agent_framework.contracts import ApprovedBaseline, FrameworkError, RunSnapshot
from backend.agent_framework.mcp_server import FrameworkTools, overview
from backend.app.security.errors import AuthError
from tests.agent_framework.test_agents_mcp import Scripted, answer, approve, call, waiting_revision
from tests.agent_framework.test_help_chat import PREFIX, chat_app, csrf_headers


SETTINGS = {'CC_A01_ENABLED': '1', 'CC_MCP_URL': 'http://127.0.0.1:18136/mcp',
            'CC_MCP_TOKEN': 't' * 32, 'CC_QWEN_BASE_URL': 'http://qwen.test/v1'}


def test_disabled_or_nonlocal_mcp_never_enables_guidance(chat_app):
    for values in ({}, {**SETTINGS, 'CC_A01_ENABLED': '0'},
                   {**SETTINGS, 'CC_MCP_URL': 'http://other.test:18136/mcp'}):
        assert run_guidance.RunGuidance.configured(values) is None
    with chat_app() as client:
        assert client.get(f'{PREFIX}/health').json().get('a01_guidance_ready') is not True
        response = client.post(f'{PREFIX}/runs/example/guidance', headers=csrf_headers(client))
        assert (response.status_code, response.json()['error']['code']) == (503, 'A01_GUIDANCE_DISABLED')


@pytest.mark.parametrize(('status', 'next_action'), [
    ('queued', 'wait_for_worker'), ('failed', 'start_new_run'), ('outcome_unknown', 'reconcile_manually'),
])
def test_guidance_is_on_demand_uses_the_session_and_scopes_mcp(chat_app, monkeypatch, status, next_action):
    context, _, _ = sample_inputs('queued-or-failed')
    failure = None if status == 'queued' else FrameworkError(
        code='PROVIDER_FAILED', message='private provider error', outcome_unknown=status == 'outcome_unknown')
    snapshot = RunSnapshot(context=context, profile='test', synthetic=True, execution_status=status, error=failure)
    facts = overview(snapshot) | {'criteria': [{'text': 'private source'}]}
    calls = []
    def explain(messages):
        assert 'private source' not in str(messages) and 'private provider error' not in str(messages)
        return answer(next_action=next_action, summary='현재 상태를 확인했습니다.')

    model = Scripted(call('get_run'), explain)
    model.close = lambda: None
    guidance = run_guidance.RunGuidance.configured(SETTINGS)
    monkeypatch.setattr(guidance, '_chat', lambda route, deadline: model)
    monkeypatch.setattr(run_guidance, 'unit_of_work', lambda _: nullcontext(None))
    monkeypatch.setattr(postgres, 'unit_of_work', lambda _: nullcontext(None))

    def get_row(connection, run_id):
        return {'snapshot_json': snapshot.model_dump(), 'input_json': {}}

    def authorise(connection, actor, context, *, write):
        assert not write and actor.user_id
        if denied:
            raise AuthError('PROJECT_ACCESS_DENIED', 403)

    def lookup(name, params):
        calls.append((name, params))
        return {'state': 'completed', 'result': facts}

    monkeypatch.setattr(run_guidance, 'McpToolClient', lambda url, token, timeout: SimpleNamespace(call=lookup))
    denied = True
    with chat_app(run_guidance=guidance) as client:
        client.app.state.framework._row = get_row
        client.app.state.framework._access = authorise
        url = f'{PREFIX}/runs/{context.run_id}/guidance'
        assert client.get(f'{PREFIX}/health').json()['a01_guidance_ready'] is True
        assert not calls
        assert client.post(url, headers=csrf_headers(client, signed_in=False)).status_code == 401
        headers = csrf_headers(client)
        assert client.post(url).status_code == 403
        assert client.post(url, headers=headers).status_code == 403
        assert not calls
        denied = False
        response = client.post(url, headers=headers)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert response.json()['next_action'] == next_action
        assert 'private source' not in response.text and 'private provider error' not in response.text
        assert calls == [('get_run', {'run_id': context.run_id})] * 3


def test_a08_compares_only_the_authorized_parent_and_completed_child(chat_app, monkeypatch):
    workflow, parent_context = waiting_revision('fail')
    child_context, _, asset = sample_inputs('a08-child', 'poster-v2', parent_context.run_id)
    workflow.rereview(parent_context.run_id, child_context, asset)
    workflow.agents = make_agents('pass')
    approve(workflow, child_context, 'approve_criteria')
    baseline = ApprovedBaseline(source_run_id='source', criteria_version_id=parent_context.criteria_version_id,
                                criteria_sha256='a' * 64, approval_id='approval', document_sha256='b' * 64)
    parent = workflow.get(parent_context.run_id).model_copy(update={'baseline': baseline})
    child = workflow.get(child_context.run_id).model_copy(update={'baseline': baseline})
    snapshots = {parent.context.run_id: parent, child.context.run_id: child}
    guidance = run_guidance.RunGuidance.configured({**SETTINGS, 'CC_A01_ENABLED': '0', 'CC_A08_ENABLED': '1'})
    resolved = [item.criterion_id for item in parent.validated.items if item.verdict != 'pass']
    model = Scripted(call('get_parent_run', 'get_child_run'), answer(summary='수정 사항을 확인했습니다.'))
    model.close = lambda: None
    monkeypatch.setattr(guidance, '_chat', lambda route, deadline: model)
    monkeypatch.setattr(run_guidance, 'unit_of_work', lambda _: nullcontext(None))
    monkeypatch.setattr(postgres, 'unit_of_work', lambda _: nullcontext(None))
    calls = []

    def lookup(name, arguments):
        assert name in ('get_run', 'get_steps')
        calls.append(arguments['run_id'])
        return FrameworkTools(workflow).call(name, arguments)

    def authorize(connection, actor, context, *, write):
        assert not write and actor.user_id
        if denied_parent and context.run_id == parent.context.run_id:
            raise AuthError('PROJECT_ACCESS_DENIED', 403)

    monkeypatch.setattr(run_guidance, 'McpToolClient', lambda url, token, timeout: SimpleNamespace(call=lookup))
    denied_parent = False
    with chat_app(run_guidance=guidance) as client:
        client.app.state.framework._row = lambda connection, id: {
            'snapshot_json': snapshots[id].model_dump(), 'input_json': {}}
        client.app.state.framework._access = authorize
        path = f'{PREFIX}/runs/{child.context.run_id}/revision-comparison'
        health = client.get(f'{PREFIX}/health').json()
        assert health['a08_comparison_ready'] and not health.get('a01_guidance_ready')
        headers = csrf_headers(client)
        assert client.post(path).status_code == 403
        denied_parent = True
        assert client.post(path, headers=headers).status_code == 403
        denied_parent = False
        snapshots[child.context.run_id] = child.model_copy(update={'execution_status': 'reviewing'})
        assert client.post(path, headers=headers).status_code == 409
        snapshots[child.context.run_id] = child
        snapshots[parent.context.run_id] = parent.model_copy(update={'baseline': None})
        assert client.post(path, headers=headers).status_code == 409
        assert not calls
        snapshots[parent.context.run_id] = parent

        response = client.post(path, headers=headers, json={'parent_run_id': 'unrelated'})
        assert response.status_code == 200
        data = response.json()
        assert data['compared'] and data['resolved'] == resolved and not data['unresolved'] and not data['new_violations']
        assert data['parent_run_id'] == parent.context.run_id and data['child_run_id'] == child.context.run_id
        assert set(calls) == set(snapshots)
