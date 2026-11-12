"""Changed criteria and affected poster reviews, without a database.

Pure comparison/selection rules and the PostgreSQL -> Neo4j -> PostgreSQL flow with the SQL reads
stubbed. The statements themselves are only compiled here; they run in db_integration environments.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from backend.agent_framework import criteria_change
from backend.agent_framework.contracts import Criterion, RequirementDetails, SourceRef
from backend.agent_framework.criteria_change import (CriteriaChangeImpact, candidates_statement, compare_criteria,
                                                     pick_previous, reviews_statement, summarize)
from backend.agent_framework.graph_store import GraphUnavailable

PROJECT = UUID('00000000-0000-4000-8000-000000000001')
DOCUMENT = UUID('00000000-0000-4000-8000-0000000000d1')
OLD_VERSION = '00000000-0000-4000-8000-0000000000c1'
NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def criterion(cid, field, expected, *, target='poster_text', label=None, text=None, details=True):
    quote = text or f'{field}: {expected}'
    source = SourceRef(document_version_id='doc-v1', page=1, block_id='b1', quote=quote,
                       source_hash=sha256(quote.encode()).hexdigest())
    scope = RequirementDetails(target=target, field=field,
                               presentation={'label': label, 'value': expected} if label else None) if details else None
    return Criterion(criterion_id=cid, text=quote, kind='text', expected=expected, operator='contains',
                     source=source, details=scope)


def test_changed_item_is_paired_by_scope_not_by_renumbered_id():
    """새 PDF는 조건 ID가 다시 매겨진다: 행사 장소가 시청 광장 → 시민공원."""
    previous = [criterion('c001', 'venue', '시청 광장', label='행사 장소'), criterion('c002', 'event_date', '11월 20일')]
    current = [criterion('c001', 'event_date', '11월 20일'), criterion('c002', 'venue', '시민공원', label='행사 장소')]
    result = compare_criteria(previous, current)
    [change] = result['changes']
    assert result['unchanged'] == 1 and result['counts'] == {'changed': 1}
    assert (change['change'], change['label'], change['target'], change['field']) == ('changed', '행사 장소', 'poster_text', 'venue')
    assert change['before']['expected'] == '시청 광장' and change['before']['criterion_id'] == 'c001'
    assert change['after']['expected'] == '시민공원' and change['after']['criterion_id'] == 'c002'


def test_changed_qualifier_is_shown_with_the_same_expected_value():
    def with_qualifier(quote):
        item = criterion('c001', 'event_date', '11월 20일', text=f'11월 20일 {quote}')
        source = item.source.model_copy(update={'quote': quote, 'source_hash': sha256(quote.encode()).hexdigest()})
        return item.model_copy(update={'details': item.details.model_copy(update={'qualifiers': (source,)})})

    previous = with_qualifier('우천 시 하루 연기')
    current = with_qualifier('우천 시 취소')
    [change] = compare_criteria([previous], [current])['changes']
    assert change['change'] == 'changed'
    assert change['before']['qualifiers'] == ['우천 시 하루 연기']
    assert change['after']['qualifiers'] == ['우천 시 취소']


def test_same_scope_with_several_items_matches_equal_values_first():
    previous = [criterion(f'c{n}', 'wording', text) for n, text in enumerate(('함께해요', '무료 입장', '선착순 100명'))]
    one_changed = [criterion('n1', 'wording', '무료 입장'), criterion('n2', 'wording', '선착순 200명'), criterion('n3', 'wording', '함께해요')]
    [change] = compare_criteria(previous, one_changed)['changes']
    assert change['change'] == 'changed' and change['label'] == '문구'
    assert (change['before']['expected'], change['after']['expected']) == ('선착순 100명', '선착순 200명')
    # 남는 수가 다르면 어느 것이 어느 것으로 바뀌었는지 단정하지 않는다
    fewer = compare_criteria(previous, [criterion('n1', 'wording', '무료 입장')])
    assert fewer['counts'] == {'removed': 2} and fewer['unchanged'] == 1
    grown = compare_criteria(previous[:1], [criterion('n1', 'wording', '새 문구'), criterion('n2', 'wording', '또 다른 문구')])
    assert grown['counts'] == {'added': 2, 'removed': 1}


def test_added_removed_and_unscoped_items():
    previous = [criterion('c1', 'venue', '시청 광장'), criterion('c2', 'x', '옛 문장', text='옛 문장', details=False)]
    current = [criterion('c1', 'venue', '시청  광장'), criterion('c2', 'logo', '주최 로고', target='poster_visual'),
               criterion('c3', 'x', '새 문장', text='새 문장', details=False)]
    result = compare_criteria(previous, current)
    assert result['unchanged'] == 1  # 공백 차이는 변경이 아니다
    assert [(c['change'], c['label'], c['field']) for c in result['changes']] == [
        ('added', '로고', 'logo'), ('added', None, None), ('removed', None, None)]
    assert compare_criteria(previous, previous) == {'changes': [], 'unchanged': 2, 'counts': {}}


def candidate(document_id, document_version_id, version_no, minutes):
    return {'document_id': document_id, 'document_version_id': document_version_id, 'document_version_no': version_no,
            'approved_at': NOW - timedelta(minutes=minutes)}


def test_previous_prefers_an_earlier_version_of_the_same_document():
    other = UUID(int=9)
    newest_elsewhere = candidate(other, UUID(int=90), 1, 1)
    v1, v2 = candidate(DOCUMENT, UUID(int=1), 1, 30), candidate(DOCUMENT, UUID(int=2), 2, 20)
    rows = [newest_elsewhere, v2, v1]  # newest approval first, as the statement orders them
    assert pick_previous(rows, DOCUMENT, UUID(int=3), 3) == (v2, 'document_revision')
    # 문서 버전 연결이 없으면(지금 화면은 업로드마다 새 문서) 같은 프로젝트의 직전 승인 기준
    assert pick_previous(rows, UUID(int=7), UUID(int=70), 1) == (newest_elsewhere, 'project_previous')
    assert pick_previous([v1], DOCUMENT, UUID(int=1), 1) == (None, None)  # 같은 문서 버전은 '이전'이 아니다
    assert pick_previous([], DOCUMENT, UUID(int=1), 1) == (None, None)


def test_statements_compile_for_postgresql():
    one = str(candidates_statement(PROJECT, 'run-b', NOW).compile(dialect=postgresql.dialect()))
    assert 'criteria_approval_bindings' in one and 'approved_at <' in one and 'LIMIT' in one
    two = reviews_statement(PROJECT, OLD_VERSION).compile(dialect=postgresql.dialect())
    assert '#>>' in str(two) and '->>' in str(two) and 'IS NOT NULL' in str(two)
    assert {('baseline', 'criteria_version_id'), 'approved_criteria_version_id', 'asset', OLD_VERSION} <= set(two.params.values())


RUNS = [{'run_id': f'poster-{n}', 'parent_run_id': None, 'asset_version_id': f'asset-{n}', 'execution_status': status,
         'machine_verdict': 'pass', 'human_decision': decision, 'created_at': NOW.isoformat(), 'decisions': []}
        for n, (status, decision) in enumerate((('completed', 'final_approved'), ('completed', 'exception_approved'),
                                                ('waiting_review', 'unreviewed')), 1)]
RECORDS = [{'approval_id': 'approval-1', 'run_id': 'poster-1', 'decision': 'approve_final'},
           {'approval_id': 'approval-2', 'run_id': 'poster-2', 'decision': 'approve_exception'}]


class Review:
    engine = None

    def __init__(self):
        self.opened = []

    def _open(self, c, subject, run_id, *, write=False):
        self.opened.append((subject, run_id, write))


class Impact(CriteriaChangeImpact):
    """SQL reads replaced by fixed rows; the flow, comparison and graph check are the real code."""
    has_previous, truncated = True, False

    def _current(self, c, subject, run_id):
        self.review._open(c, subject, run_id)
        return {'project_id': PROJECT, 'created_at': NOW, 'document_id': DOCUMENT, 'document_version_no': 2,
                'document_version_id': UUID(int=2), 'criteria_version_id': None, 'version_no': None,
                'criteria': [criterion('c001', 'venue', '시민공원', label='행사 장소')]}

    def _previous(self, c, run_id, current):
        return {'basis': 'document_revision', 'criteria': [criterion('c009', 'venue', '시청 광장', label='행사 장소')],
                'criteria_version_id': OLD_VERSION, 'version_no': 1, 'source_run_id': 'run-a',
                'document_version_id': str(UUID(int=1)), 'document_version_no': 1, 'approval_id': 'approval-0',
                'approved_at': NOW.isoformat()} if self.has_previous else None

    def _reviews(self, c, project_id, version_id):
        assert (project_id, version_id) == (PROJECT, OLD_VERSION)
        return RUNS, RECORDS, self.truncated


class Graph:
    def __init__(self, *, revision=7, runs=RUNS, approvals=RECORDS, error=None, truncated=False):
        self.answer = {'revision': revision, 'truncated': truncated,
                       'runs': [{'run_id': r['run_id'], 'status': r['execution_status'],
                                 'asset_version_id': r['asset_version_id']} for r in runs],
                       'approvals': list(approvals)}
        self.error, self.calls = error, []

    def review_impact(self, project_id, criteria_version_id):
        self.calls.append((project_id, criteria_version_id))
        if self.error:
            raise GraphUnavailable(self.error)
        return self.answer


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    @contextmanager
    def unit_of_work(engine):
        yield None
    monkeypatch.setattr(criteria_change, 'unit_of_work', unit_of_work)
    monkeypatch.setattr(criteria_change, 'postgres_revision', lambda c, project_id: 7)


def test_current_graph_names_the_same_reviews_and_postgres_supplies_the_facts():
    graph, review = Graph(), Review()
    result = Impact(review, graph).read('user', 'run-b')
    assert graph.calls == [(str(PROJECT), OLD_VERSION)]
    assert result['answered_by'] == 'neo4j' and result['warnings'] == []
    assert result['graph'] == {'status': 'complete', 'graph_revision': 7, 'postgres_revision': 7}
    # "행사 장소가 변경됐어요. 이전 기준을 사용한 포스터 검수 3건 중 2건이 승인됐어요."
    assert [(c['label'], c['before']['expected'], c['after']['expected']) for c in result['changes']] == [
        ('행사 장소', '시청 광장', '시민공원')]
    assert result['summary'] == {'affected_runs': 3, 'approved': 2, 'by_status': {'completed': 2, 'waiting_review': 1}}
    assert result['affected_runs'] == RUNS and result['previous']['basis'] == 'document_revision'
    assert 'criteria' not in result['previous'] and result['current']['criteria'] == 1
    assert review.opened == [('user', 'run-b', False)] * 2  # 그래프 조회 뒤 접근 권한을 다시 확인한다


@pytest.mark.parametrize('graph,warning,status', [
    (None, 'GRAPH_NOT_CONFIGURED', 'unavailable'),
    (Graph(error='GRAPH_UNAVAILABLE'), 'GRAPH_UNAVAILABLE', 'unavailable'),
    (Graph(error='GRAPH_APPROVED_CRITERIA_UNAVAILABLE'), 'GRAPH_APPROVED_CRITERIA_UNAVAILABLE', 'unavailable'),
    (Graph(revision=6), 'GRAPH_STALE', 'stale'),
    (Graph(truncated=True), 'GRAPH_RESULT_LIMIT', 'stale'),
    (Graph(runs=RUNS[:2]), 'GRAPH_RELATION_MISMATCH', 'stale'),
    (Graph(approvals=RECORDS[:1]), 'GRAPH_RELATION_MISMATCH', 'stale'),
])
def test_postgres_answers_whenever_the_graph_cannot_be_trusted(graph, warning, status):
    result = Impact(Review(), graph).read('user', 'run-b')
    assert result['answered_by'] == 'postgres_fallback' and result['warnings'] == [warning]
    assert result['graph']['status'] == status
    assert result['summary']['affected_runs'] == 3 and result['summary']['approved'] == 2  # 같은 답


def test_cut_lists_and_missing_previous_criteria():
    impact = Impact(Review(), Graph())
    impact.truncated = True
    cut = impact.read('user', 'run-b')
    assert cut['answered_by'] == 'postgres_fallback' and cut['warnings'] == ['RESULT_LIMIT'] and cut['truncated']
    graph = Graph()
    first = Impact(Review(), graph)
    first.has_previous = False
    result = first.read('user', 'run-b')
    assert graph.calls == [] and result['previous'] is None and result['changes'] == []
    assert result['answered_by'] == 'postgres' and result['summary'] == summarize([])
