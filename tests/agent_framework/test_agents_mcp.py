"""A01/A08 agents and framework MCP tools with a scripted model; no real Qwen call."""
from contextlib import contextmanager
import json
import socket
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.agent_framework.adapters.synthetic import make_agents, sample_inputs
from backend.agent_framework.agents import RereviewAgent, RunCoordinator
from backend.agent_framework.agents.rereview import compare_verdicts
from backend.agent_framework.contracts import Approval
from backend.agent_framework.mcp_server import FrameworkTools, McpToolClient, McpToolError, create_mcp_app
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.runtime import Workflow

TOKEN = 'test-only-agent-framework-token-32chars'


class Scripted:
    def __init__(self, *steps):
        self.steps, self.tool_names = list(steps), []

    def complete(self, messages, tools):
        self.tool_names.append([tool['function']['name'] for tool in tools])
        step = self.steps.pop(0)
        return step(messages) if callable(step) else step


def call(*names):
    return {'content': None, 'tool_calls': [{'id': f'call-{index}', 'type': 'function',
            'function': {'name': name, 'arguments': '{}'}} for index, name in enumerate(names)]}


def answer(**data):
    return {'content': '```json\n' + json.dumps(data, ensure_ascii=False) + '\n```'}


def receipts(messages):
    return [json.loads(m['content']) for m in messages if m['role'] == 'tool']


def approve(workflow, context, decision, **fields):
    return workflow.approve(context.run_id, Approval(context=context, actor_id=context.actor_id,
                                                     decision=decision, **fields))


def started(scenario='pass', *decisions, workflow_type=Workflow):
    workflow = workflow_type(make_agents(scenario))
    context, document, asset = sample_inputs()
    workflow.start(context, document, asset)
    for decision in decisions:
        approve(workflow, context, decision, **({'reason': '수정 요청'} if decision == 'approve_changes' else {}))
    return workflow, context


def waiting_revision(scenario='fail', **kwargs):
    return started(scenario, 'approve_criteria', 'approve_changes', **kwargs)


def test_mcp_transport_exposes_no_approval_tool_and_requires_token():
    workflow, context = started()
    headers = {'Authorization': f'Bearer {TOKEN}', 'Accept': 'application/json, text/event-stream'}
    with TestClient(create_mcp_app(workflow, token=TOKEN, host='testserver')) as client:
        assert client.post('/mcp', json={}).status_code == 401
        response = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                       'clientInfo': {'name': 'agent-framework-test', 'version': '1'}}})
        assert response.status_code == 200, response.text
        listed = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}).json()
        names = {tool['name'] for tool in listed['result']['tools']}
        assert names == {'get_run', 'get_steps', 'get_approval_history', 'start_run', 'request_rereview'}
        result = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': 'get_run', 'arguments': {'run_id': context.run_id}}}).json()['result']
        parsed = result.get('structuredContent') or json.loads(result['content'][0]['text'])
        assert parsed['state'] == 'completed'
        assert parsed['result']['execution_status'] == 'waiting_criteria'
    with pytest.raises(ValueError):
        create_mcp_app(workflow, token='short')


def test_tool_receipts_map_workflow_errors():
    tools = FrameworkTools(Workflow(make_agents()))
    assert tools.call('get_run', {'run_id': 'missing'})['error']['code'] == 'RUN_NOT_FOUND'
    assert tools.call('approve', {})['error']['code'] == 'UNKNOWN_TOOL'
    assert tools.call('get_run', {})['error']['code'] == 'INVALID_ARGUMENTS'
    assert tools.call('start_run', {'context': {}, 'document': {}, 'asset': {}})['error']['code'] == 'INVALID_INPUT'


def test_a01_explains_state_with_read_tools_only():
    workflow, context = started()
    model = Scripted(call('get_run', 'get_steps'),
                     answer(next_action='request_criteria_approval', summary='조건 초안 확정이 필요합니다.'))
    note = RunCoordinator(model, FrameworkTools(workflow)).assess(context.run_id)
    assert note.next_action == 'request_criteria_approval'
    assert note.waiting_on == 'human' and note.execution_status == 'waiting_criteria'
    assert note.tools_used == ('get_run', 'get_steps') and note.synthetic is True
    assert set(model.tool_names[0]) == {'get_run', 'get_steps', 'get_approval_history'}
    assert workflow.get(context.run_id).approvals == ()


def test_a01_waiting_asset_after_pdf_approval():
    workflow, context = started()

    class ApprovedPDF(FrameworkTools):
        def get_run(self, run_id):
            return super().get_run(run_id) | {'execution_status': 'waiting_asset'}

    model = Scripted(answer(next_action='submit_poster', summary='포스터를 등록해 주세요.'))
    note = RunCoordinator(model, ApprovedPDF(workflow)).assess(context.run_id)
    assert (note.next_action, note.waiting_on) == ('submit_poster', 'creator')


def test_a01_rejects_action_the_state_does_not_allow():
    workflow, context = started()
    model = Scripted(answer(next_action='request_final_approval', summary='최종 승인하세요.'))
    with pytest.raises(AdapterFailure) as error:
        RunCoordinator(model, FrameworkTools(workflow)).assess(context.run_id)
    assert error.value.code == 'AGENT_ACTION_NOT_ALLOWED'


def test_invalid_answer_gets_one_format_correction():
    workflow, context = started()
    prose = {'content': '조건 초안 확정이 필요합니다.'}
    model = Scripted(prose, answer(next_action='request_criteria_approval', summary='확정이 필요합니다.'))
    assert RunCoordinator(model, FrameworkTools(workflow)).assess(context.run_id).next_action == 'request_criteria_approval'
    with pytest.raises(AdapterFailure) as error:
        RunCoordinator(Scripted(prose, prose), FrameworkTools(workflow)).assess(context.run_id)
    assert error.value.code == 'AGENT_OUTPUT_INVALID'


def test_a01_unknown_run_never_reaches_model():
    model = Scripted()
    with pytest.raises(AdapterFailure) as error:
        RunCoordinator(model, FrameworkTools(Workflow(make_agents()))).assess('missing')
    assert error.value.code == 'RUN_NOT_FOUND' and model.tool_names == []


def test_a08_requests_rereview_once_with_bound_arguments():
    workflow, parent = waiting_revision()
    child, _, asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    seen = []

    def finish(messages):
        seen.extend(receipts(messages))
        return answer(rereview_requested=True, summary='재검수 실행을 만들었습니다.')

    model = Scripted(call('get_parent_run', 'get_parent_approvals'),
                     call('request_rereview', 'request_rereview'), finish)
    summary = RereviewAgent(model, FrameworkTools(workflow)).request(parent.run_id, child, asset)
    assert seen[-2]['state'] == 'completed'
    assert seen[-1]['error']['code'] == 'WRITE_ALREADY_DONE'
    assert summary.rereview_requested and summary.child_run_id == 'demo-run-2'
    assert summary.child_status == 'waiting_criteria'
    assert workflow.get('demo-run-2').context.parent_run_id == parent.run_id


def test_a08_created_child_survives_invalid_followup():
    workflow, parent = waiting_revision()
    context, _, asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    model = Scripted(call('get_parent_run', 'request_rereview'), {'content': 'invalid'}, {'content': 'invalid'})
    result = RereviewAgent(model, FrameworkTools(workflow)).request(parent.run_id, context, asset)
    assert result.rereview_requested and result.child_run_id == context.run_id
    assert result.child_status == workflow.get(context.run_id).execution_status == 'waiting_criteria'
    assert result.tools_used == ('get_parent_run', 'request_rereview')


def test_a08_rejected_request_keeps_the_error_code():
    workflow, parent = started('fail', 'approve_criteria')
    child, _, new_asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    model = Scripted(call('request_rereview'), answer(rereview_requested=False, summary='거절되었습니다.'))
    summary = RereviewAgent(model, FrameworkTools(workflow)).request(parent.run_id, child, new_asset)
    assert summary.rereview_requested is False and summary.child_run_id is None
    assert summary.request_error.code == 'WORKFLOW_CONFLICT' and 'WORKFLOW_CONFLICT' in summary.summary
    assert summary.model_summary == '거절되었습니다.'
    with pytest.raises(KeyError):
        workflow.get('demo-run-2')


def test_a08_invalid_denial_gets_request_specific_correction_without_a_write():
    workflow, parent = started('fail', 'approve_criteria')
    child, _, asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    invalid = answer(rereview_requested=False, unresolved=["human_decision != 'changes_approved'"],
                     summary='수정 요청 승인이 필요합니다.')

    def corrected(messages):
        instruction = messages[-1]['content']
        assert all(name in instruction for name in ('resolved', 'unresolved', 'new_violations', 'summary'))
        assert '[]' in instruction
        return answer(rereview_requested=False, summary='수정 요청 승인이 없어 재검수를 요청하지 않았습니다.')

    model = Scripted(invalid, corrected)
    result = RereviewAgent(model, FrameworkTools(workflow)).request(parent.run_id, child, asset)
    assert not result.rereview_requested and result.child_run_id is None and result.request_error is None
    assert not result.resolved and not result.unresolved and not result.new_violations
    assert 'request_rereview' not in result.tools_used and len(model.tool_names) == 2
    with pytest.raises(KeyError):workflow.get(child.run_id)


@pytest.mark.parametrize('reason,expected',[
    ("human_decision != 'changes_approved'", 'AGENT_OUTPUT_INVALID'),
    ('approval_missing', 'AGENT_OUTPUT_INCONSISTENT'),
])
def test_a08_denial_does_not_silently_discard_invalid_comparison_lists(reason, expected):
    workflow, parent = started('fail', 'approve_criteria')
    child, _, asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    invalid = answer(rereview_requested=False, unresolved=[reason], summary='수정 요청 승인이 필요합니다.')
    model = Scripted(invalid, invalid)
    with pytest.raises(AdapterFailure) as error:
        RereviewAgent(model, FrameworkTools(workflow)).request(parent.run_id, child, asset)
    assert error.value.code == expected
    assert len(model.tool_names) == (2 if expected == 'AGENT_OUTPUT_INVALID' else 1)
    with pytest.raises(KeyError):workflow.get(child.run_id)


def test_a08_claimed_request_must_match_executed_command():
    workflow, parent = waiting_revision()
    child, _, asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    model = Scripted(answer(rereview_requested=True, summary='요청했습니다.'),
                     answer(rereview_requested=True, summary='요청했습니다.'))
    with pytest.raises(AdapterFailure) as error:
        RereviewAgent(model, FrameworkTools(workflow)).request(parent.run_id, child, asset)
    assert error.value.code == 'AGENT_OUTPUT_INCONSISTENT'
    with pytest.raises(KeyError):
        workflow.get('demo-run-2')


def test_a08_claimed_request_gets_one_correction_and_then_calls_the_tool():
    workflow, parent = waiting_revision()
    child, _, asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    model = Scripted(answer(rereview_requested=True, summary='요청했습니다.'), call('request_rereview'),
                     answer(rereview_requested=True, summary='요청했습니다.'))
    summary = RereviewAgent(model, FrameworkTools(workflow)).request(parent.run_id, child, asset)
    assert summary.rereview_requested and summary.child_run_id == 'demo-run-2'
    assert model.tool_names[1] == ['get_parent_run', 'get_parent_approvals', 'request_rereview']


def test_a08_ambiguous_write_is_outcome_unknown_and_not_retried():
    workflow, parent = waiting_revision()
    child, _, asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)

    class Broken(FrameworkTools):
        writes = 0

        def call(self, name, arguments):
            if name == 'request_rereview':
                Broken.writes += 1
                raise ConnectionError('transport lost')
            return super().call(name, arguments)

    model = Scripted(call('request_rereview'))
    with pytest.raises(AdapterFailure) as error:
        RereviewAgent(model, Broken(workflow)).request(parent.run_id, child, asset)
    assert error.value.outcome_unknown is True and Broken.writes == 1


def test_a08_compare_matches_recorded_verdicts():
    workflow, parent = waiting_revision('fail')
    child, _, asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    workflow.rereview(parent.run_id, child, asset)
    workflow.agents = make_agents('pass')
    approve(workflow, child, 'approve_criteria')
    tools = FrameworkTools(workflow)
    compared, resolved, unresolved, new = compare_verdicts(tools.get_run(parent.run_id), tools.get_run(child.run_id))
    assert compared and resolved and not unresolved and not new
    model = Scripted(call('get_parent_run', 'get_child_run'),
                     answer(resolved=list(resolved), summary='수정 요청 항목이 모두 해결되었습니다.'))
    summary = RereviewAgent(model, tools).compare(parent.run_id, child.run_id)
    assert summary.compared and summary.resolved == resolved and summary.child_status == 'waiting_review'
    wrong = Scripted(answer(unresolved=list(resolved), summary='해결되지 않았습니다.'))
    with pytest.raises(AdapterFailure) as error:
        RereviewAgent(wrong, tools).compare(parent.run_id, child.run_id)
    assert error.value.code == 'AGENT_OUTPUT_INCONSISTENT'


def test_a08_compare_rejects_unrelated_child():
    workflow, context = started()
    with pytest.raises(AdapterFailure) as error:
        RereviewAgent(Scripted(), FrameworkTools(workflow)).compare('other-parent', context.run_id)
    assert error.value.code == 'RUN_SCOPE_MISMATCH'


@contextmanager
def mcp_server(workflow):
    """Real loopback HTTP MCP server, so McpToolClient runs its actual transport."""
    import uvicorn

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_mcp_app(workflow, token=TOKEN), host='127.0.0.1', port=port,
                                           log_level='warning', lifespan='on'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError('MCP test server did not start')
        time.sleep(0.05)
    try:
        yield f'http://127.0.0.1:{port}/mcp'
    finally:
        server.should_exit = True
        thread.join(10)


def test_mcp_client_reads_and_maps_workflow_rejections_over_http():
    workflow, parent = waiting_revision()
    with mcp_server(workflow) as url:
        client = McpToolClient(url, TOKEN, timeout=10)
        run = client.call('get_run', {'run_id': parent.run_id})
        assert run['state'] == 'completed' and run['result']['human_decision'] == 'changes_approved'
        history = client.call('get_approval_history', {'run_id': parent.run_id})
        assert [item['decision'] for item in history['result']] == ['approve_criteria', 'approve_changes']
        assert client.call('get_run', {'run_id': 'missing'})['error']['code'] == 'RUN_NOT_FOUND'
        child, _, asset = sample_inputs('demo-run-2', 'poster-v1', parent.run_id)  # Same asset version.
        rejected = client.call('request_rereview', {'parent_run_id': parent.run_id,
            'context': child.model_dump(mode='json'), 'asset': asset.model_dump(mode='json')})
        assert rejected['state'] == 'failed' and rejected['error']['code'] == 'WORKFLOW_CONFLICT'
    with pytest.raises(KeyError):
        workflow.get('demo-run-2')


def test_mcp_client_rejects_a_wrong_token():
    workflow, parent = waiting_revision()
    with mcp_server(workflow) as url:
        with pytest.raises(httpx.HTTPStatusError) as error:
            McpToolClient(url, 'wrong-token-' + 'x' * 32, timeout=10).call('get_run', {'run_id': parent.run_id})
    assert error.value.response.status_code == 401


def test_mcp_tool_exception_and_empty_result_are_outcome_unknown(monkeypatch):
    class Exploding(Workflow):
        def get(self, run_id):
            if run_id == 'explode':
                raise RuntimeError('provider detail must not leak')
            return super().get(run_id)

    with mcp_server(Exploding(make_agents())) as url:
        client = McpToolClient(url, TOKEN, timeout=10)
        with pytest.raises(McpToolError):
            client.call('get_run', {'run_id': 'explode'})
        from mcp import ClientSession, types

        async def empty(self, name, arguments=None, *args, **kwargs):
            return types.CallToolResult(content=[])

        monkeypatch.setattr(ClientSession, 'call_tool', empty)
        with pytest.raises(McpToolError):
            client.call('get_run', {'run_id': 'any'})


def test_a08_requests_rereview_through_the_mcp_client():
    workflow, parent = waiting_revision()
    child, _, asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    model = Scripted(call('get_parent_run'), call('request_rereview'),
                     answer(rereview_requested=True, summary='재검수 실행을 만들었습니다.'))
    with mcp_server(workflow) as url:
        summary = RereviewAgent(model, McpToolClient(url, TOKEN, timeout=10)).request(parent.run_id, child, asset)
    assert summary.rereview_requested and summary.child_run_id == 'demo-run-2'
    assert summary.child_status == 'waiting_criteria' and summary.request_error is None
    assert workflow.get('demo-run-2').context.parent_run_id == parent.run_id


def test_a08_write_applied_then_tool_error_is_outcome_unknown_over_mcp():
    class AppliedThenFailed(Workflow):
        def rereview(self, *args, **kwargs):
            super().rereview(*args, **kwargs)
            raise RuntimeError('response lost after the child run was created')

    workflow, parent = waiting_revision(workflow_type=AppliedThenFailed)
    child, _, new_asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    with mcp_server(workflow) as url:
        with pytest.raises(AdapterFailure) as error:
            RereviewAgent(Scripted(call('request_rereview')), McpToolClient(url, TOKEN, timeout=10)).request(
                parent.run_id, child, new_asset)
    assert error.value.code == 'TOOL_OUTCOME_UNKNOWN' and error.value.outcome_unknown is True
    assert workflow.get('demo-run-2').context.parent_run_id == parent.run_id  # The write did happen.
