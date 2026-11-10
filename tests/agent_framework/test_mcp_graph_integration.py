"""Relationship tools over a real PostgreSQL 17 and Neo4j 5: the team's projector writes, MCP only reads.

Run through scripts/framework_graph_test.py. Reuses the poster pipeline fixture
(scripted A02 and poster observations, development-only approvals; not Qwen, not
a client decision).
"""
import pytest

from backend.app.persistence.transaction import unit_of_work
from backend.agent_framework.graph_projection import mark_project_changed
from backend.agent_framework.graph_store import FrameworkGraph
from backend.agent_framework.mcp_evidence import PostgresEvidence
from backend.agent_framework.mcp_postgres import McpStore, PostgresRuns
from backend.agent_framework.mcp_server import FrameworkTools
from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database, csrf  # noqa: F401
from tests.agent_framework.test_pdf_postgres_integration import (  # noqa: F401
    document_database as initial_pdf_database, runtime, upload)
from tests.agent_framework.test_criteria_postgres_integration import document_database  # noqa: F401
from tests.agent_framework.test_a02_postgres_integration import a02, admit, register  # noqa: F401
from tests.agent_framework.test_poster_postgres_integration import (  # noqa: F401
    approve, create_review, image, pipeline, post, review_body, run_worker)
from tests.agent_framework.test_graph_postgres_integration import (  # noqa: F401
    admitted, graph_database, graph_target, rag, synced)

pytestmark = pytest.mark.db_integration
GRAPH_FIELDS = ('answered_by', 'graph', 'warnings')


def tools(r, graph):
    store = McpStore(r.db.engine, r.config)
    return FrameworkTools(PostgresRuns(store), PostgresEvidence(store, graph))


def answer(toolset, name, run_id):
    receipt = toolset.call(name, {'run_id': run_id})
    assert receipt['state'] == 'completed', receipt
    return receipt['result']


def without_graph_fields(result):
    return {key: value for key, value in result.items() if key not in GRAPH_FIELDS}


def reviews(r):
    """A failing poster review, its approved change request and the revised child run."""
    first = create_review(r, image(r, 'wrong_date'))
    assert approve(r, first, 'approve_criteria').status_code == 200
    failed, outcome, _ = run_worker(r)
    assert outcome == 'completed' and failed['machine_verdict'] == 'fail'
    assert approve(r, failed, 'approve_changes').status_code == 200
    failed_id = failed['context']['run_id']
    response = post(r.client, '/api/framework/v1/runs/' + failed_id + '/poster-revisions',
                    review_body(r, image(r, 'revised')))
    assert response.status_code == 201, response.text
    return failed_id, response.json()['context']['run_id']


def counts(graph, project_id):
    projected = graph.read_projection(str(project_id))
    return projected['revision'], len(projected['nodes']), len(projected['edges'])


def test_a_current_projection_answers_and_postgres_gives_the_same_answer(rag):
    r = rag
    run_id = admitted(r)
    synced(r)
    current = answer(tools(r, r.graph), 'get_relations', run_id)
    assert current['answered_by'] == 'neo4j' and current['graph']['status'] == 'complete' and current['warnings'] == []
    assert current['graph']['graph_revision'] == current['graph']['postgres_revision']
    kinds = {node['kind'] for node in current['nodes']}
    assert {'run', 'criteria_version', 'criterion', 'source', 'poster_version', 'document_version'} <= kinds
    assert 'project' not in kinds
    fallback = answer(tools(r, None), 'get_relations', run_id)
    assert fallback['answered_by'] == 'postgres_fallback' and fallback['warnings'] == ['GRAPH_NOT_CONFIGURED']
    assert without_graph_fields(fallback) == without_graph_fields(current)


def test_impact_lists_the_poster_review_but_not_the_criteria_run(rag):
    r = rag
    run_id = admitted(r)
    synced(r)
    impact = answer(tools(r, r.graph), 'get_change_impact', run_id)
    assert impact['answered_by'] == 'neo4j' and impact['warnings'] == []
    assert impact['source_run_id'] != run_id
    assert [item['run_id'] for item in impact['affected_runs']] == [run_id]
    assert impact['document_changed'] is False and impact['newer_document_versions'] == []
    assert without_graph_fields(answer(tools(r, None), 'get_change_impact', run_id)) == without_graph_fields(impact)


def test_a_revised_review_and_a_new_document_version_reach_both_answers(rag):
    r = rag
    failed_id, child = reviews(r)
    replaced = upload(r, r.source_raw, document=r.registered['document_id'],
                      previous=r.registered['document']['document_version_id'])
    assert replaced.status_code == 201, replaced.text
    synced(r)
    impact = answer(tools(r, r.graph), 'get_change_impact', child)
    assert impact['answered_by'] == 'neo4j'
    assert [item['run_id'] for item in impact['affected_runs']] == [failed_id, child]
    assert impact['document_changed'] is True and len(impact['newer_document_versions']) == 1
    relations = answer(tools(r, r.graph), 'get_relations', child)
    assert {'source': 'run:' + failed_id, 'kind': 'reinspected_as', 'target': 'run:' + child} in relations['edges']
    assert without_graph_fields(answer(tools(r, None), 'get_change_impact', child)) == without_graph_fields(impact)


def test_a_stale_or_unreachable_projection_is_never_used(rag):
    r = rag
    run_id = admitted(r)
    synced(r)
    with unit_of_work(r.db.engine) as c:
        mark_project_changed(c, r.pid, 'graph.rebuild_requested', r.pid)
    stale = answer(tools(r, r.graph), 'get_relations', run_id)
    assert stale['answered_by'] == 'postgres_fallback' and stale['warnings'] == ['GRAPH_STALE']
    assert stale['graph']['status'] == 'stale' and stale['graph']['graph_revision'] < stale['graph']['postgres_revision']
    down = FrameworkGraph('bolt://127.0.0.1:1', 'neo4j', 'unit-only')
    try:
        unreachable = answer(tools(r, down), 'get_relations', run_id)
    finally:
        down.close()
    assert unreachable['answered_by'] == 'postgres_fallback' and unreachable['warnings'] == ['GRAPH_UNAVAILABLE']
    assert without_graph_fields(unreachable) == without_graph_fields(stale)


def test_mcp_tools_leave_the_projection_untouched(rag):
    r = rag
    run_id = admitted(r)
    synced(r)
    before = counts(r.graph, r.pid)
    toolset = tools(r, r.graph)
    answer(toolset, 'get_relations', run_id)
    answer(toolset, 'get_change_impact', run_id)
    assert counts(r.graph, r.pid) == before
