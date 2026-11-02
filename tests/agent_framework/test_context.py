"""Approved provenance is common input, not a configurable retrieval agent."""
from dataclasses import fields
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from backend.agent_framework.adapters.synthetic import sample_inputs, make_agents
from backend.agent_framework.composition import ROLE_PORTS, TeamConfiguration
from backend.agent_framework.context import freeze_approved_context
from backend.agent_framework.contracts import Approval, ExtractRequest
from backend.agent_framework.invocation import ROLE_CONTRACTS
from backend.agent_framework.ports import Agents
from backend.agent_framework.runtime import Workflow


def inputs():
    context, document, asset = sample_inputs()
    draft = make_agents().extractor.extract(ExtractRequest(context=context, document=document, synthetic=True))
    return context, document, asset, draft


def test_only_four_roles_and_retired_role_is_rejected():
    assert set(ROLE_PORTS) == set(ROLE_CONTRACTS) == {'A02', 'A04_A05', 'A06', 'A07'}
    assert [f.name for f in fields(Agents)] == ['extractor', 'inspector', 'validator', 'reporter']
    roles = {role: {'kind': 'unconfigured'} for role in ROLE_PORTS}
    roles['A03'] = {'kind': 'factory', 'factory': 'retired_module:create'}
    with pytest.raises(ValidationError):
        TeamConfiguration(roles=roles)


def test_freeze_preserves_approved_sources_without_network_or_model_calls():
    context, _, _, draft = inputs()
    with patch('socket.socket', side_effect=AssertionError('No external lookup')):
        bundle = freeze_approved_context(context, draft.criteria, synthetic=True)
    assert bundle == freeze_approved_context(context, draft.criteria, synthetic=True)
    assert bundle.context == context
    assert bundle.graph_revision is None
    assert [e.source for e in bundle.evidence] == [c.source for c in draft.criteria]
    assert all(e.kind == 'current_requirement' for e in bundle.evidence)
    assert 'no PostgreSQL/Neo4j' in bundle.warnings[0]
    next_run = context.model_copy(update={'run_id': 'next-run'})
    assert freeze_approved_context(next_run, draft.criteria).context_id != bundle.context_id
    criterion = draft.criteria[0].model_copy(update={'expected': 'new approved expected'})
    assert freeze_approved_context(context, (criterion, *draft.criteria[1:])).context_id != bundle.context_id


def test_freeze_rejects_wrong_document_or_forged_source_hash():
    context, _, _, draft = inputs()
    for change in ({'document_version_id': 'old-document'}, {'source_hash': '0' * 64}):
        source = draft.criteria[0].source.model_copy(update=change)
        criterion = draft.criteria[0].model_copy(update={'source': source})
        with pytest.raises(ValueError):
            freeze_approved_context(context, (criterion,))


def test_criteria_approval_freezes_context_without_a_separate_worker_step():
    context, document, asset, _ = inputs()
    workflow = Workflow(make_agents())
    before = workflow.start(context, document, asset)
    assert before.evidence is None
    approval = Approval(context=context, decision='approve_criteria', actor_id=context.actor_id)
    queued = workflow.approve(context.run_id, approval, defer=True)
    assert queued.execution_status == 'queued'
    assert queued.evidence == freeze_approved_context(context, queued.criteria.criteria, synthetic=True)
    assert [step.role for step in queued.step_records] == ['A02']
