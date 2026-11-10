"""Relationship answers without databases: walking the projection, freshness states, row rechecks.

The identifiers below are structural test fixtures, not review data. A real
Neo4j and PostgreSQL are covered by test_mcp_graph_integration.py (db_integration).
"""
import pytest

from backend.agent_framework import mcp_graph
from backend.agent_framework.graph_store import FrameworkGraph, GraphUnavailable
from backend.agent_framework.mcp_evidence import PostgresEvidence
from backend.agent_framework.mcp_graph import (impact_from, neighborhood, postgres_revision, read_current,
                                               relations_from, to_graph)

NODES = [
    {'key': 'project:p', 'kind': 'project'},
    {'key': 'document:d1', 'kind': 'document_version', 'sha256': 'x'},
    {'key': 'document:d2', 'kind': 'document_version', 'sha256': 'x'},
    {'key': 'document:d3', 'kind': 'document_version', 'sha256': 'x'},
    {'key': 'document:other', 'kind': 'document_version', 'sha256': 'x'},
    {'key': 'criteria:c1', 'kind': 'criteria_version', 'approved': True, 'sha256': 'x'},
    {'key': 'criterion:c1:c001', 'kind': 'criterion', 'criterion_id': 'c001'},
    {'key': 'source:c1:c001:document', 'kind': 'source', 'source_hash': 'x'},
    {'key': 'run:source', 'kind': 'run', 'state': 'waiting_asset', 'synthetic': True},
    {'key': 'run:poster-1', 'kind': 'run', 'state': 'waiting_revision', 'synthetic': True},
    {'key': 'run:poster-2', 'kind': 'run', 'state': 'waiting_review', 'synthetic': True},
    {'key': 'run:other', 'kind': 'run', 'state': 'queued', 'synthetic': True},
    {'key': 'poster:a1', 'kind': 'poster_version', 'sha256': 'x'},
    {'key': 'approval:a1', 'kind': 'approval', 'decision': 'approve_criteria'},
]
EDGES = [{'source': source, 'target': target, 'kind': kind} for source, target, kind in (
    ('project:p', 'document:d1', 'contains'), ('project:p', 'document:other', 'contains'),
    ('project:p', 'run:source', 'contains'), ('project:p', 'run:other', 'contains'),
    ('document:d1', 'document:d2', 'revised_as'), ('document:d2', 'document:d3', 'revised_as'),
    ('criteria:c1', 'document:d1', 'based_on'), ('criteria:c1', 'criterion:c1:c001', 'contains'),
    ('criterion:c1:c001', 'source:c1:c001:document', 'sourced_from'),
    ('run:source', 'document:d1', 'reads'), ('run:source', 'criteria:c1', 'applies'),
    ('run:poster-1', 'criteria:c1', 'applies'), ('run:poster-1', 'poster:a1', 'inspects'),
    ('run:poster-2', 'criteria:c1', 'applies'), ('run:poster-1', 'run:poster-2', 'reinspected_as'),
    ('approval:a1', 'criteria:c1', 'approves'), ('run:other', 'document:other', 'reads'),
)]


@pytest.fixture
def graph():
    return to_graph(NODES, EDGES)


def test_the_project_hub_is_dropped_so_a_walk_stays_on_related_rows(graph):
    assert 'project:p' not in graph.nodes
    assert all('project:p' not in (source, target) for source, _kind, target in graph.edges)
    assert list(graph.edges) == sorted(graph.edges)
    # run:other shares only the project with the poster reviews.
    assert 'run:other' not in neighborhood(graph, 'run:poster-2')


def test_details_keep_state_and_decision_but_no_hashes(graph):
    assert graph.nodes['run:poster-2'] == {'kind': 'run', 'detail': 'waiting_review'}
    assert graph.nodes['approval:a1'] == {'kind': 'approval', 'detail': 'approve_criteria'}
    assert graph.nodes['criterion:c1:c001'] == {'kind': 'criterion', 'detail': 'c001'}
    assert graph.nodes['source:c1:c001:document'] == {'kind': 'source', 'detail': ''}


def test_neighborhood_follows_links_both_ways_up_to_the_depth(graph):
    assert neighborhood(graph, 'run:poster-2', depth=1) == {'run:poster-2', 'run:poster-1', 'criteria:c1'}
    assert neighborhood(graph, 'run:missing') == set()


def test_relations_are_sorted_and_limited_with_the_start_kept(graph, monkeypatch):
    answer = relations_from(graph, 'run:poster-2')
    ids = [node['id'] for node in answer['nodes']]
    assert ids == sorted(ids) and not answer['truncated']
    assert {'id': 'approval:a1', 'kind': 'approval', 'detail': 'approve_criteria'} in answer['nodes']
    assert {'id': 'poster:a1', 'kind': 'poster_version'} in answer['nodes']
    assert {'source': 'run:poster-1', 'kind': 'reinspected_as', 'target': 'run:poster-2'} in answer['edges']
    monkeypatch.setattr(mcp_graph, 'NODE_LIMIT', 3)
    small = relations_from(graph, 'run:poster-2')
    kept = {node['id'] for node in small['nodes']}
    assert small['truncated'] and len(kept) == 3 and 'run:poster-2' in kept
    assert all(edge['source'] in kept and edge['target'] in kept for edge in small['edges'])


def test_impact_follows_revisions_forward_only(graph):
    assert impact_from(graph, 'criteria:c1', 'document:d1') == (
        ['run:poster-1', 'run:poster-2', 'run:source'], ['document:d2', 'document:d3'])
    assert impact_from(graph, 'criteria:c1', 'document:d2')[1] == ['document:d3']
    assert impact_from(graph, 'criteria:missing', 'document:d3') == ([], [])


class Projection:
    def __init__(self, revision=7, *, unmanaged=False, error=None):
        self.revision, self.unmanaged, self.error, self.asked = revision, unmanaged, error, []

    def read_projection(self, project_id):
        self.asked.append(project_id)
        if self.error:
            raise self.error
        return {'revision': self.revision, 'nodes': NODES, 'edges': EDGES, 'unmanaged': self.unmanaged}


def state(status, graph_revision=None):
    return {'status': status, 'graph_revision': graph_revision, 'postgres_revision': 7}


@pytest.mark.parametrize('reader,expected,warnings', [
    (None, state('unavailable'), ['GRAPH_NOT_CONFIGURED']),
    (Projection(None), state('unavailable'), ['GRAPH_NOT_PROJECTED']),
    (Projection(6), state('stale', 6), ['GRAPH_STALE']),
    (Projection(8), state('stale', 8), ['GRAPH_STALE']),
    (Projection(unmanaged=True), state('stale', 7), ['GRAPH_UNMANAGED_RELATIONSHIPS']),
    (Projection(error=GraphUnavailable()), state('unavailable'), ['GRAPH_UNAVAILABLE']),
    (Projection(error=GraphUnavailable('GRAPH_SCHEMA_NOT_READY')), state('unavailable'), ['GRAPH_SCHEMA_NOT_READY']),
])
def test_only_a_clean_projection_at_the_postgres_revision_answers(reader, expected, warnings):
    assert read_current(reader, 'p', 7) == (expected, warnings, None)


def test_a_current_projection_answers_and_is_asked_for_that_project_only():
    reader = Projection()
    found, warnings, graph = read_current(reader, 'p', 7)
    assert found == state('complete', 7) and warnings == [] and 'run:source' in graph.nodes
    assert reader.asked == ['p']


def test_nothing_is_read_before_the_project_has_a_revision():
    reader = Projection()
    assert read_current(reader, 'p', 0)[1] == ['GRAPH_NOT_PROJECTED'] and reader.asked == []


def test_a_graph_without_settings_reports_itself_and_postgres_answers():
    assert read_current(FrameworkGraph('', 'neo4j', ''), 'p', 7)[1:] == (['GRAPH_NOT_CONFIGURED'], None)


def test_postgres_revision_works_before_af005_is_installed(monkeypatch):
    monkeypatch.setattr(mcp_graph, 'installed', lambda c: False)
    assert postgres_revision(None, 'p') == 0
    monkeypatch.setattr(mcp_graph, 'installed', lambda c: True)
    monkeypatch.setattr(mcp_graph, 'graph_status', lambda c, pid: {'required_revision': 7})
    assert postgres_revision(None, 'p') == 7


def test_a_projection_missing_the_asked_node_is_stale():
    evidence = PostgresEvidence.__new__(PostgresEvidence)
    evidence.graph = Projection()
    found, warnings, graph = evidence._projection('p', 7, 'run:missing')
    assert found == state('stale', 7) and warnings == ['GRAPH_NODE_MISSING'] and graph is None
    assert evidence._projection('p', 7, 'run:source')[2] is not None


def test_only_snapshot_derived_nodes_skip_the_row_recheck():
    assert PostgresEvidence._absent(None, 'p', ['poster:a1', 'criterion:c1:c001', 'source:c1:c001:document']) is False
    assert PostgresEvidence._absent(None, 'p', ['unexpected:1']) is True
