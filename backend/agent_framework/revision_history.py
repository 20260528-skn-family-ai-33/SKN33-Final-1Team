"""Revision review history of one poster: original review -> re-reviews -> their approval records.

Read only. The chain is (review)-reinspected_as->(re-review) with (approval)-approves->(review),
exactly the relations graph_projection writes. PostgreSQL -> Neo4j -> PostgreSQL, like the MCP
relation tools: the projection answers only at PostgreSQL's revision and only if every run and
approval it names is in PostgreSQL; otherwise the same walk runs over the PostgreSQL snapshot
('postgres_fallback'). Each entry's facts (status, records, criteria version) come from PostgreSQL.

Entry state follows the approval records linked to that review: a final approval record makes it
final_approved, a change approval makes it changes_requested, and a finished review without a
decision record is awaiting_decision. The uploaded file's client name is not stored, so entries
carry asset_version_id (the poster image) instead of a file name.
"""
from __future__ import annotations

from collections import deque
from uuid import UUID

from sqlalchemy import select

from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from .contracts import RunSnapshot
from .criteria_review import versions
from .mcp_graph import Graph, load_graph, postgres_revision, read_current, run_node
from .postgres import approvals, runs

CHAIN_LIMIT = 100  # Reviews followed in one chain; `truncated` says when more exist.
DECISION_STATE = (('approve_final', 'final_approved'), ('approve_exception', 'exception_approved'),
                  ('approve_changes', 'changes_requested'))


def chain_from(graph: Graph, start: str) -> tuple[list[str], dict[str, list[str]], bool]:
    """(review nodes from the original, approval nodes per review, truncated) around `start`."""
    if start not in graph.nodes:
        return [], {}, False
    parent, children, approving = {}, {}, {}
    for source, kind, target in graph.edges:
        if kind == 'reinspected_as':
            parent[target] = source
            children.setdefault(source, []).append(target)
        elif kind == 'approves' and graph.nodes[source]['kind'] == 'approval':
            approving.setdefault(target, []).append(source)
    root, seen = start, {start}
    while root in parent and parent[root] not in seen and len(seen) < CHAIN_LIMIT:
        root = parent[root]
        seen.add(root)
    chain, queue = [], deque([root])
    while queue and len(chain) < CHAIN_LIMIT:
        current = queue.popleft()
        chain.append(current)
        queue.extend(sorted(children.get(current, ())))
    return chain, {node: sorted(approving.get(node, ())) for node in chain}, bool(queue)


def review_state(execution_status: str, decisions: list[str]) -> str:
    """Screen state of one review from its linked approval records, then its execution status."""
    for decision, state in DECISION_STATE:
        if decision in decisions:
            return state
    if execution_status == 'waiting_review':
        return 'awaiting_decision'
    if execution_status in ('failed', 'outcome_unknown'):
        return 'failed'
    return 'completed' if execution_status == 'completed' else 'in_progress'


def summarize(entries: list[dict]) -> dict:
    final = next((entry for entry in reversed(entries) if entry['state'] == 'final_approved'), None)
    return {'reviews': len(entries), 'rereviews': max(0, len(entries) - 1),
            'final_approved': final and {'run_id': final['run_id'], 'revision_no': final['revision_no']},
            'awaiting_decision': any(entry['state'] == 'awaiting_decision' for entry in entries)}


class RevisionHistory:
    def __init__(self, store, graph=None):
        self.store, self.engine = store, store.engine
        self.graph = graph  # graph_store.FrameworkGraph or None (PostgreSQL then answers alone)

    def _asked(self, c, subject, run_id):
        row = self.store._row(c, run_id)
        snapshot = RunSnapshot.model_validate(row['snapshot_json'])
        self.store._access(c, subject, snapshot.context, write=False)
        if row['input_json'].get('asset') is None:
            raise AuthError('REVISION_HISTORY_REQUIRES_POSTER_REVIEW', 409)
        return row

    @staticmethod
    def _rows(c, project_id, run_ids: list[str]) -> tuple[dict, dict, dict]:
        """(run rows, approval records per run, criteria version numbers) for these runs in this project."""
        found = {row['run_id']: row for row in c.execute(
            select(runs.c.run_id, runs.c.parent_run_id, runs.c.input_json, runs.c.snapshot_json, runs.c.created_at)
            .where(runs.c.project_id == project_id, runs.c.run_id.in_(run_ids))).mappings()}
        records: dict[str, list[dict]] = {}
        for row in c.execute(select(approvals).where(approvals.c.project_id == project_id, approvals.c.run_id.in_(run_ids))
                             .order_by(approvals.c.approved_at, approvals.c.approval_id)).mappings():
            records.setdefault(row['run_id'], []).append({
                'approval_id': str(row['approval_id']), 'decision': row['decision'],
                'reason': row['payload_json'].get('reason') or '', 'actor_id': str(row['actor_id']),
                'approved_at': row['approved_at'].isoformat()})
        pinned = set()
        for row in found.values():
            try:
                pinned.add(UUID((row['input_json'].get('baseline') or {}).get('criteria_version_id', '')))
            except ValueError:
                continue
        numbers = {str(row['criteria_version_id']): row['version_no'] for row in c.execute(
            select(versions.c.criteria_version_id, versions.c.version_no)
            .where(versions.c.project_id == project_id, versions.c.criteria_version_id.in_(pinned))).mappings()}
        return found, records, numbers

    @staticmethod
    def _parent_links(graph, chain) -> set[tuple[str, str]]:
        members = set(chain)
        return {(source, target) for source, kind, target in graph.edges
                if kind == 'reinspected_as' and target in members}

    def read(self, subject, run_id: str) -> dict:
        with unit_of_work(self.engine) as c:
            project_id = self._asked(c, subject, run_id)['project_id']
            revision = postgres_revision(c, project_id)
        start = run_node(run_id)
        graph, warnings, projected = read_current(self.graph, project_id, revision)
        if projected is not None and start not in projected.nodes:
            graph, warnings, projected = graph | {'status': 'stale'}, ['GRAPH_NODE_MISSING'], None
        with unit_of_work(self.engine) as c:
            self._asked(c, subject, run_id)  # Access is rechecked after the graph call.
            authority = load_graph(c, project_id)
            chain, approving, truncated = chain_from(authority, start)
            found, records, numbers = self._rows(c, project_id, [node.partition(':')[2] for node in chain])
            answer = 'postgres_fallback'
            if projected is not None:
                actual_revision = postgres_revision(c, project_id)
                if actual_revision != revision:
                    graph, warnings = graph | {'status': 'stale', 'postgres_revision': actual_revision}, ['GRAPH_STALE']
                elif truncated:
                    graph, warnings = graph | {'status': 'stale'}, ['GRAPH_RESULT_LIMIT']
                elif (chain_from(projected, start) == (chain, approving, truncated)
                      and self._parent_links(projected, chain) == self._parent_links(authority, chain)):
                    answer = 'neo4j'
                else:
                    graph, warnings = graph | {'status': 'stale'}, ['GRAPH_REFERS_TO_ABSENT_ROWS']
        entries = []
        for row in sorted(found.values(), key=lambda item: (item['created_at'], item['run_id'])):
            snapshot = RunSnapshot.model_validate(row['snapshot_json'])
            baseline = row['input_json'].get('baseline') or {}
            mine = records.get(row['run_id'], [])
            entries.append({
                'run_id': row['run_id'], 'revision_no': len(entries), 'parent_run_id': row['parent_run_id'],
                'current': row['run_id'] == run_id,
                'asset_version_id': (row['input_json'].get('asset') or {}).get('asset_version_id'),
                'criteria_version_id': baseline.get('criteria_version_id'),
                'criteria_version_no': numbers.get(baseline.get('criteria_version_id')),
                'execution_status': snapshot.execution_status, 'machine_verdict': snapshot.machine_verdict,
                'human_decision': snapshot.human_decision,
                'state': review_state(snapshot.execution_status, [record['decision'] for record in mine]),
                'created_at': row['created_at'].isoformat(), 'records': mine})
        return {'run_id': run_id, 'answered_by': answer, 'graph': graph, 'warnings': warnings,
                'entries': entries, 'summary': summarize(entries), 'truncated': truncated}
