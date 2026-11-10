"""Relationship answers for the MCP tools, read from the team's graph projection.

graph_projection owns the graph: PostgreSQL events go through the outbox and
graph-projector into AFEntity/AF_RELATION, and PostgreSQL holds the revision the
projection must reach. This module never writes. It reads one project's nodes
and edges (Neo4j when its revision is current, otherwise the same snapshot
function over PostgreSQL) and walks them in Python, so both sources give the
same shape of answer.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .graph_projection import graph_status, installed, project_snapshot
from .graph_store import GraphUnavailable

DEPTH = 4  # Links walked around a run by get_relations.
NODE_LIMIT = 300  # Nodes returned by get_relations; `truncated` says when more exist.
NEWER_LIMIT = 50  # Document revisions followed by get_change_impact.
# The project node links to every document and run; walking through it would return the whole project.
_HUB = 'project'
_DETAIL = {'run': 'state', 'approval': 'decision', 'criterion': 'criterion_id'}


@dataclass(frozen=True)
class Graph:
    nodes: dict  # entity key -> {'kind', 'detail'}
    edges: tuple  # (source, kind, target), sorted


def run_node(run_id) -> str:
    return f'run:{run_id}'


def criteria_node(version_id) -> str:
    return f'criteria:{version_id}'


def document_node(version_id) -> str:
    return f'document:{version_id}'


def to_graph(nodes, edges) -> Graph:
    """Same input from graph_store.read_projection and graph_projection.project_snapshot."""
    shown = {node['key']: {'kind': node['kind'], 'detail': str(node.get(_DETAIL.get(node['kind']), ''))}
             for node in nodes if node['kind'] != _HUB}
    return Graph(shown, tuple(sorted((edge['source'], edge['kind'], edge['target']) for edge in edges
                                     if edge['source'] in shown and edge['target'] in shown)))


def load_graph(c, project_id) -> Graph:
    return to_graph(*project_snapshot(c, project_id))


def postgres_revision(c, project_id) -> int:
    """The revision the projection must reach; 0 when nothing was queued or AF005 is not installed."""
    return graph_status(c, project_id)['required_revision'] if installed(c) else 0


def read_current(graph, project_id, revision: int) -> tuple[dict, list, Graph | None]:
    """(graph state, warnings, Graph). No Graph means PostgreSQL must answer."""
    state = {'status': 'unavailable', 'graph_revision': None, 'postgres_revision': revision}
    if graph is None:
        return state, ['GRAPH_NOT_CONFIGURED'], None
    if not revision:
        return state, ['GRAPH_NOT_PROJECTED'], None
    try:
        projected = graph.read_projection(str(project_id))
    except GraphUnavailable as error:
        return state, [error.code], None
    stored = projected['revision']
    if stored is None:
        return state, ['GRAPH_NOT_PROJECTED'], None
    state['graph_revision'] = stored
    if stored != revision:
        return state | {'status': 'stale'}, ['GRAPH_STALE'], None
    if projected['unmanaged']:
        return state | {'status': 'stale'}, ['GRAPH_UNMANAGED_RELATIONSHIPS'], None
    return state | {'status': 'complete'}, [], to_graph(projected['nodes'], projected['edges'])


def neighborhood(graph: Graph, start: str, depth: int = DEPTH) -> set:
    """Nodes within `depth` links of start in either direction."""
    if start not in graph.nodes:
        return set()
    around = {}
    for source, _kind, target in graph.edges:
        around.setdefault(source, set()).add(target)
        around.setdefault(target, set()).add(source)
    seen, queue = {start: 0}, deque([start])
    while queue:
        current = queue.popleft()
        if seen[current] == depth:
            continue
        for other in around.get(current, ()):
            if other not in seen:
                seen[other] = seen[current] + 1
                queue.append(other)
    return set(seen)


def relations_from(graph: Graph, start: str) -> dict:
    """Sorted and limited; the start node is always kept."""
    found = neighborhood(graph, start)
    kept = sorted(found)
    if len(kept) > NODE_LIMIT:
        kept = [start] + [node for node in kept if node != start][:NODE_LIMIT - 1]
    keep = set(kept)
    return {'nodes': [{'id': node, 'kind': graph.nodes[node]['kind']}
                      | ({'detail': graph.nodes[node]['detail']} if graph.nodes[node]['detail'] else {})
                      for node in sorted(keep)],
            'edges': [{'source': source, 'kind': kind, 'target': target} for source, kind, target in graph.edges
                      if source in keep and target in keep],
            'truncated': len(found) > NODE_LIMIT}


def impact_from(graph: Graph, criteria: str, document: str) -> tuple[list, list]:
    """Runs applying the criteria version, and document versions revised from its source."""
    applying = sorted(source for source, kind, target in graph.edges if kind == 'applies' and target == criteria)
    newer, frontier = set(), {document}
    for _ in range(NEWER_LIMIT):
        frontier = {target for source, kind, target in graph.edges if kind == 'revised_as' and source in frontier} - newer
        if not frontier:
            break
        newer |= frontier
    return applying, sorted(newer)
