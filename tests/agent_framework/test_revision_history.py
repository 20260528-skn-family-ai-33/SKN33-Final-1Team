"""Revision review history without a database: the chain walk, entry states and the graph recheck."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from backend.agent_framework import revision_history
from backend.agent_framework.contracts import Context, RunSnapshot
from backend.agent_framework.graph_store import GraphUnavailable
from backend.agent_framework.mcp_graph import to_graph
from backend.agent_framework.revision_history import RevisionHistory, chain_from, review_state, summarize
from backend.app.security.errors import AuthError

PROJECT = UUID('00000000-0000-4000-8000-000000000001')
ACTOR = '00000000-0000-4000-8000-0000000000a1'
CRITERIA = '00000000-0000-4000-8000-0000000000c1'
NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)
# 원본 검수 → 1차 수정본 → 2차 수정본. 앞의 둘은 수정 요청, 마지막은 최종 승인(공유 문서의 예시).
REVIEWS = (('poster-0', None, 'waiting_revision', 'changes_approved', [('approval-1', 'approve_criteria', ''),
                                                                         ('approval-2', 'approve_changes', '행사 날짜 표기 수정')]),
           ('poster-1', 'poster-0', 'waiting_revision', 'changes_approved', [('approval-3', 'approve_changes', '주최 로고 위치 수정')]),
           ('poster-2', 'poster-1', 'completed', 'final_approved', [('approval-4', 'approve_final', '수정 사항 확인')]))


def snapshot(run_id, parent, status, decision):
    context = Context(project_id=str(PROJECT), run_id=run_id, actor_id=ACTOR, document_version_id='doc-v1',
                      criteria_version_id=CRITERIA, asset_version_id='asset-' + run_id, parent_run_id=parent)
    return RunSnapshot(context=context, profile='test', synthetic=True, execution_status=status, human_decision=decision)


def run_row(run_id, parent, status, decision, order, asset=True):
    return {'run_id': run_id, 'parent_run_id': parent, 'project_id': PROJECT, 'created_at': NOW + timedelta(hours=order),
            'input_json': {'asset': {'asset_version_id': 'asset-' + run_id} if asset else None,
                           'baseline': {'criteria_version_id': CRITERIA}},
            'snapshot_json': snapshot(run_id, parent, status, decision).model_dump(mode='json')}


ROWS = {item[0]: run_row(*item[:4], order) for order, item in enumerate(REVIEWS)}
RECORDS = {run_id: [{'approval_id': approval_id, 'decision': decision, 'reason': reason, 'actor_id': ACTOR,
                     'approved_at': NOW.isoformat()} for approval_id, decision, reason in records]
           for run_id, _parent, _status, _decision, records in REVIEWS}


def projection(reviews=REVIEWS, *, drop_approval=None):
    """Nodes and edges in the shape graph_projection.project_snapshot and read_projection return."""
    nodes = [{'key': 'project:' + str(PROJECT), 'kind': 'project'}, {'key': 'criteria:' + CRITERIA, 'kind': 'criteria_version'},
             {'key': 'run:criteria-run', 'kind': 'run', 'state': 'waiting_asset'}]
    edges = [{'source': 'approval:approval-0', 'target': 'criteria:' + CRITERIA, 'kind': 'approves'}]
    nodes.append({'key': 'approval:approval-0', 'kind': 'approval', 'decision': 'approve_criteria'})
    for run_id, parent, status, _decision, records in reviews:
        nodes.append({'key': 'run:' + run_id, 'kind': 'run', 'state': status})
        edges.append({'source': 'run:' + run_id, 'target': 'criteria:' + CRITERIA, 'kind': 'applies'})
        if parent:
            edges.append({'source': 'run:' + parent, 'target': 'run:' + run_id, 'kind': 'reinspected_as'})
        for approval_id, decision, _reason in records:
            if approval_id == drop_approval:
                continue
            nodes.append({'key': 'approval:' + approval_id, 'kind': 'approval', 'decision': decision})
            edges.append({'source': 'approval:' + approval_id, 'target': 'run:' + run_id, 'kind': 'approves'})
    return nodes, edges


def test_chain_starts_at_the_original_whichever_review_is_asked():
    graph = to_graph(*projection())
    for start in ('run:poster-0', 'run:poster-1', 'run:poster-2'):
        chain, approving, truncated = chain_from(graph, start)
        assert chain == ['run:poster-0', 'run:poster-1', 'run:poster-2'] and not truncated
        assert approving == {'run:poster-0': ['approval:approval-1', 'approval:approval-2'],
                             'run:poster-1': ['approval:approval-3'], 'run:poster-2': ['approval:approval-4']}
    assert chain_from(graph, 'run:criteria-run') == (['run:criteria-run'], {'run:criteria-run': []}, False)
    assert chain_from(graph, 'run:absent') == ([], {}, False)


@pytest.mark.parametrize('status,decisions,state', [
    ('completed', ['approve_criteria', 'approve_final'], 'final_approved'),
    ('waiting_revision', ['approve_criteria', 'approve_changes'], 'changes_requested'),
    ('completed', ['approve_exception'], 'exception_approved'),
    ('waiting_review', ['approve_criteria'], 'awaiting_decision'),   # 재검수는 끝났지만 담당자 결정이 없다
    ('reviewing', ['approve_criteria'], 'in_progress'), ('queued', [], 'in_progress'),
    ('failed', [], 'failed'), ('completed', [], 'completed'),
])
def test_state_follows_the_linked_approval_records(status, decisions, state):
    assert review_state(status, decisions) == state


class Store:
    engine = None

    def __init__(self, error=None):
        self.error, self.access = error, []

    def _row(self, c, run_id):
        if run_id == 'criteria-run':
            return run_row('criteria-run', None, 'waiting_asset', 'unreviewed', 0, asset=False)
        if run_id not in ROWS:
            raise AuthError('FRAMEWORK_RUN_NOT_FOUND', 404)
        return ROWS[run_id]

    def _access(self, c, subject, context, *, write=True):
        self.access.append((subject, context.run_id, write))
        if self.error:
            raise self.error


class History(RevisionHistory):
    """SQL reads replaced by fixed rows; the walk, recheck and entries are the real code."""
    rows = ROWS

    def _rows(self, c, project_id, run_ids):
        assert project_id == PROJECT
        return ({run_id: self.rows[run_id] for run_id in run_ids if run_id in self.rows},
                {run_id: RECORDS[run_id] for run_id in run_ids if run_id in RECORDS}, {CRITERIA: 1})


class Graph:
    def __init__(self, nodes_edges=None, *, revision=5, error=None, unmanaged=False):
        nodes, edges = nodes_edges or projection()
        self.answer, self.error = {'revision': revision, 'nodes': nodes, 'edges': edges, 'unmanaged': unmanaged}, error

    def read_projection(self, project_id):
        assert project_id == str(PROJECT)
        if self.error:
            raise GraphUnavailable(self.error)
        return self.answer


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    @contextmanager
    def unit_of_work(engine):
        yield None
    monkeypatch.setattr(revision_history, 'unit_of_work', unit_of_work)
    monkeypatch.setattr(revision_history, 'postgres_revision', lambda c, project_id: 5)
    monkeypatch.setattr(revision_history, 'load_graph', lambda c, project_id: to_graph(*projection()))


def test_history_matches_the_shared_example():
    store = Store()
    result = History(store, Graph()).read('user', 'poster-1')
    assert result['answered_by'] == 'neo4j' and result['warnings'] == [] and not result['truncated']
    assert result['graph'] == {'status': 'complete', 'graph_revision': 5, 'postgres_revision': 5}
    assert result['summary'] == {'reviews': 3, 'rereviews': 2, 'awaiting_decision': False,
                                 'final_approved': {'run_id': 'poster-2', 'revision_no': 2}}
    assert [(e['run_id'], e['revision_no'], e['state'], e['current']) for e in result['entries']] == [
        ('poster-0', 0, 'changes_requested', False), ('poster-1', 1, 'changes_requested', True),
        ('poster-2', 2, 'final_approved', False)]
    last = result['entries'][-1]
    assert last['asset_version_id'] == 'asset-poster-2' and last['criteria_version_no'] == 1
    assert last['records'] == RECORDS['poster-2'] and last['parent_run_id'] == 'poster-1'
    assert store.access == [('user', 'poster-1', False)] * 2  # 그래프 조회 뒤 접근 권한을 다시 확인한다


def test_finished_rereview_without_a_decision_waits_for_the_manager(monkeypatch):
    waiting = dict(ROWS, **{'poster-2': run_row('poster-2', 'poster-1', 'waiting_review', 'unreviewed', 2)})
    reviews = REVIEWS[:2] + (('poster-2', 'poster-1', 'waiting_review', 'unreviewed', []),)
    monkeypatch.setattr(revision_history, 'load_graph', lambda c, project_id: to_graph(*projection(reviews)))
    history = History(Store(), Graph(projection(reviews)))
    history.rows = waiting
    original = dict(RECORDS)
    RECORDS.pop('poster-2')
    try:
        result = history.read('user', 'poster-2')
    finally:
        RECORDS.update(original)
    assert result['answered_by'] == 'neo4j' and result['entries'][-1]['state'] == 'awaiting_decision'
    assert result['summary']['final_approved'] is None and result['summary']['awaiting_decision']


@pytest.mark.parametrize('graph,warning', [
    (None, 'GRAPH_NOT_CONFIGURED'),
    (Graph(error='GRAPH_UNAVAILABLE'), 'GRAPH_UNAVAILABLE'),
    (Graph(revision=4), 'GRAPH_STALE'),
    (Graph(unmanaged=True), 'GRAPH_UNMANAGED_RELATIONSHIPS'),
    (Graph(projection(REVIEWS[:1])), 'GRAPH_NODE_MISSING'),                       # 요청한 검수가 그래프에 없다
    (Graph(projection(REVIEWS[:2])), 'GRAPH_REFERS_TO_ABSENT_ROWS'),              # 요청한 검수의 자식이 빠졌다
    (Graph(projection(REVIEWS[:2] + ((REVIEWS[2][0], 'poster-0', *REVIEWS[2][2:]),))),
     'GRAPH_REFERS_TO_ABSENT_ROWS'),                                             # 실행은 모두 있으나 부모가 다르다
    (Graph(projection(drop_approval='approval-4')), 'GRAPH_REFERS_TO_ABSENT_ROWS'),  # 승인 기록이 PostgreSQL과 다르다
])
def test_postgres_snapshot_answers_whenever_the_graph_cannot_be_trusted(graph, warning):
    result = History(Store(), graph).read('user', 'poster-1')
    assert result['answered_by'] == 'postgres_fallback' and result['warnings'] == [warning]
    assert [e['state'] for e in result['entries']] == ['changes_requested', 'changes_requested', 'final_approved']
    assert result['summary']['rereviews'] == 2


def test_refuses_criteria_runs_and_unauthorized_readers():
    with pytest.raises(AuthError) as refused:
        History(Store(), Graph()).read('user', 'criteria-run')
    assert refused.value.code == 'REVISION_HISTORY_REQUIRES_POSTER_REVIEW'
    with pytest.raises(AuthError) as denied:
        History(Store(AuthError('FORBIDDEN', 403)), Graph()).read('user', 'poster-1')
    assert denied.value.code == 'FORBIDDEN'
    assert summarize([]) == {'reviews': 0, 'rereviews': 0, 'final_approved': None, 'awaiting_decision': False}
