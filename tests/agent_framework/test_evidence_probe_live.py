"""Evidence probes over a real PostgreSQL 17 run history; the Qwen run needs a model endpoint.

Both tests use the poster pipeline fixture (scripted A02 and poster observations,
development-only approvals; not a client decision): a failed review, its approved
change request, the revised child run and a newer document version. Probes ask
about the child run. Needs -m db_integration (and a POSIX host for PDF storage).

- test_probes_hold_on_real_tool_results: no model. A scripted model calls the
  truth tool and repeats it, so the grading code meets real tool results.
- test_live_qwen_answers_evidence_questions: also -m live_model and
  AGENT_FRAMEWORK_QWEN_BASE_URL (optionally AGENT_FRAMEWORK_QWEN_API_KEY).
  EVIDENCE_PROBE_OUT names a JSON file for the per-question results.
Relations come from PostgreSQL here (no Neo4j); the answers are the same.
"""
import json
import os

import pytest

from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database, csrf  # noqa: F401
from tests.app.auth_fixtures import auth_environment  # noqa: F401
from tests.agent_framework.test_pdf_postgres_integration import (  # noqa: F401
    document_database as initial_pdf_database, runtime, upload)
from tests.agent_framework.test_criteria_postgres_integration import document_database  # noqa: F401
from tests.agent_framework.test_a02_postgres_integration import a02  # noqa: F401
from tests.agent_framework.test_poster_postgres_integration import pipeline  # noqa: F401
from tests.agent_framework.test_mcp_graph_integration import reviews
from tests.agent_framework.test_agents_mcp import answer, call
from tests.agent_framework.evidence_probe import PROBES, run_probe, write_results
from backend.agent_framework.agents import OpenAICompatibleChat
from backend.agent_framework.mcp_evidence import PostgresEvidence
from backend.agent_framework.mcp_postgres import McpStore, PostgresRuns
from backend.agent_framework.mcp_server import FrameworkTools

SCENE = ('synthetic poster pipeline fixture: failed review, approved change request, '
         'revised child run, newer document version; probes ask about the child run')


@pytest.fixture
def scene(pipeline):
    r = pipeline
    failed_id, child = reviews(r)
    replaced = upload(r, r.source_raw, document=r.registered['document_id'],
                      previous=r.registered['document']['document_version_id'])
    assert replaced.status_code == 201, replaced.text
    store = McpStore(r.db.engine, r.config)
    return FrameworkTools(PostgresRuns(store), PostgresEvidence(store)), failed_id, child


class Oracle:
    """Calls the probe's truth tool, then repeats exactly what it returned."""

    def __init__(self, probe, run_id):
        self.probe, self.run_id = probe, run_id

    def complete(self, messages, tools):
        receipts = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        if not receipts:
            return call(self.probe.truth_tool)
        return answer(summary='scripted', **self.probe.facts(receipts[-1]['result'], self.run_id))


@pytest.mark.db_integration
def test_probes_hold_on_real_tool_results(scene):
    tools, failed_id, child = scene
    results = {p.name: run_probe(Oracle(p, child), tools, child, p) for p in PROBES}
    assert all(r.passed for r in results.values()), results
    # The scene gives non-trivial answers, so a model cannot pass by echoing defaults.
    assert results['sources'].expected['status'] == 'stale'
    assert results['impact'].expected == {'affected_run_ids': sorted([failed_id, child]), 'document_changed': True}
    assert results['relations'].expected['parent_run_id'] == failed_id
    assert results['history'].expected['approved_criteria_version_id'] == results['relations'].expected[
        'pinned_criteria_version_id']
    assert len(results['criteria'].expected['criterion_ids']) >= 1


@pytest.fixture
def model():
    url = os.environ.get('AGENT_FRAMEWORK_QWEN_BASE_URL')
    if not url:
        pytest.skip('AGENT_FRAMEWORK_QWEN_BASE_URL is not configured')
    chat = OpenAICompatibleChat(url, os.environ.get('AGENT_FRAMEWORK_QWEN_API_KEY'))
    yield chat
    chat.close()


@pytest.mark.db_integration
@pytest.mark.live_model
def test_live_qwen_answers_evidence_questions(scene, model, tmp_path):
    tools, _failed_id, child = scene
    results = [run_probe(model, tools, child, p) for p in PROBES]
    write_results(os.environ.get('EVIDENCE_PROBE_OUT') or tmp_path / 'evidence-probe.json', results, scene=SCENE)
    for r in results:
        print(f'{r.name}: passed={r.passed} tools={list(r.tools_used)} mismatched={list(r.mismatched)} error={r.error}')
    assert all(r.passed for r in results), [(r.name, r.mismatched, r.grounded, r.error) for r in results if not r.passed]
