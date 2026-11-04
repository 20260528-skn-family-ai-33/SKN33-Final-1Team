"""A01/A08 against a real Qwen3.5-4B vLLM endpoint; excluded by default (live_model).

Set AGENT_FRAMEWORK_QWEN_BASE_URL (and optionally AGENT_FRAMEWORK_QWEN_API_KEY),
then run: pytest -m live_model tests/agent_framework/test_agents_live.py
Workflow adapters stay synthetic; only the agent reasoning uses the real model.
"""
import os

import pytest

from backend.agent_framework.adapters.synthetic import make_agents, sample_inputs
from backend.agent_framework.agents import OpenAICompatibleChat, RereviewAgent, RunCoordinator
from backend.agent_framework.mcp_server import FrameworkTools
from tests.agent_framework.test_agents_mcp import approve, started, waiting_revision

pytestmark = pytest.mark.live_model


@pytest.fixture
def model():
    url = os.environ.get('AGENT_FRAMEWORK_QWEN_BASE_URL')
    if not url:
        pytest.skip('AGENT_FRAMEWORK_QWEN_BASE_URL is not configured')
    chat = OpenAICompatibleChat(url, os.environ.get('AGENT_FRAMEWORK_QWEN_API_KEY'))
    yield chat
    chat.close()


def test_live_a01_waiting_criteria(model):
    workflow, context = started()
    note = RunCoordinator(model, FrameworkTools(workflow)).assess(context.run_id)
    assert note.next_action == 'request_criteria_approval'


def test_live_a01_failed_review(model):
    workflow, context = started('fail', 'approve_criteria')
    note = RunCoordinator(model, FrameworkTools(workflow)).assess(context.run_id)
    assert note.next_action in {'request_change_approval', 'request_exception_approval'}


def test_live_a08_request_and_compare(model):
    workflow, parent = waiting_revision()
    child, _, new_asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    agent = RereviewAgent(model, FrameworkTools(workflow))
    requested = agent.request(parent.run_id, child, new_asset)
    assert requested.rereview_requested and requested.child_run_id == child.run_id
    workflow.agents = make_agents('pass')
    approve(workflow, child, 'approve_criteria')
    compared = agent.compare(parent.run_id, child.run_id)
    assert compared.compared and compared.resolved and not compared.unresolved


def test_live_a08_declines_without_approved_change(model):
    workflow, parent = started('fail', 'approve_criteria')
    child, _, new_asset = sample_inputs('demo-run-2', 'poster-v2', parent.run_id)
    summary = RereviewAgent(model, FrameworkTools(workflow)).request(parent.run_id, child, new_asset)
    assert summary.rereview_requested is False
    # A rejected write also ends as False; request_error proves the model never tried it.
    assert summary.request_error is None and 'request_rereview' not in summary.tools_used
    with pytest.raises(KeyError):
        workflow.get(child.run_id)
