"""What changed against the previously approved criteria, and which poster reviews used those.

Read only; no model call, no approval, nothing written. Two questions, two stores:

* Changed items: the two criteria records are compared from PostgreSQL. A new document
  re-extracts its criteria and renumbers their IDs, so items are paired by inspection
  scope (details.target/field), not by criterion ID.
* Affected reviews: approved criteria -> review run -> poster version -> approval record.
  PostgreSQL -> Neo4j -> PostgreSQL, like the MCP relation tools: the fixed Cypher
  graph_store.review_impact answers only when it is at PostgreSQL's revision and names
  exactly the rows PostgreSQL holds; otherwise PostgreSQL answers ('postgres_fallback').
  Every returned fact (status, decision) is read from PostgreSQL either way.

"Previous" is the approved criteria of an earlier version of the same document. The
front currently registers each upload as a new document, so without that link it falls
back to the latest criteria approved in the same project before this run ('basis' says which).
"""
from __future__ import annotations

import unicodedata
from collections import Counter
from uuid import UUID

from sqlalchemy import or_, select

from backend.app.persistence.transaction import unit_of_work
from .contracts import RunSnapshot
from .criteria_review import bindings, versions
from .documents import versions as documents
from .graph_store import GraphUnavailable
from .mcp_graph import postgres_revision
from .postgres import approvals, runs

CANDIDATES = 200  # Earlier approved criteria looked at when choosing "previous".
RUN_LIMIT = 500   # Affected reviews returned; `truncated` says when more exist.
APPROVED = ('final_approved', 'exception_approved')
FIELD_LABELS = {
    'wording': '문구', 'title_phrase': '제목', 'event_name': '행사명', 'year': '연도', 'event_date': '행사일',
    'start_date': '시작일', 'end_date': '종료일', 'event_period': '행사 기간', 'weekday': '요일', 'venue': '행사 장소',
    'viewing_hours': '관람 시간', 'visual_requirements': '시각 요구사항', 'logo': '로고', 'visual_color': '색상',
    'composition': '구성', 'imagery': '이미지', 'pixel_width': '가로 픽셀', 'pixel_height': '세로 픽셀',
    'file_format': '파일 형식', 'color_mode': '색상 모드', 'quantity': '수량',
}


def _flat(text: str) -> str:
    return ' '.join(unicodedata.normalize('NFC', text or '').split())


def _scope(criterion) -> tuple[str, str]:
    """Pairing key across documents; criteria without a known scope pair only on identical text."""
    details = criterion.details
    if details is not None and details.field != 'unspecified':
        return details.target, details.field
    return '', _flat(criterion.text)


def _qualifiers(criterion) -> list[str]:
    return [source.quote for source in criterion.details.qualifiers] if criterion.details else []


def _value(criterion) -> tuple:
    qualifiers = frozenset(_flat(quote) for quote in _qualifiers(criterion))
    return criterion.kind, criterion.operator, _flat(criterion.expected), criterion.unit or '', criterion.required, qualifiers


def _shown(criterion) -> dict:
    return {'criterion_id': criterion.criterion_id, 'text': criterion.text, 'expected': criterion.expected,
            'unit': criterion.unit, 'operator': criterion.operator, 'required': criterion.required,
            'qualifiers': _qualifiers(criterion)}


def _label(*criteria) -> str | None:
    for criterion in criteria:
        details = criterion.details if criterion is not None else None
        if details is not None and details.presentation is not None:
            return details.presentation.label
    for criterion in criteria:
        details = criterion.details if criterion is not None else None
        if details is not None and details.field in FIELD_LABELS:
            return FIELD_LABELS[details.field]
    return None


def compare_criteria(previous, current) -> dict:
    """Changed, added and removed items between two criteria lists (pure).

    Within one scope, items with the same comparison value are unchanged. If exactly one
    item is left on each side it changed; otherwise the leftovers are removed and added.
    """
    groups: dict[tuple, tuple[list, list]] = {}
    for side, items in enumerate((current, previous)):  # Current first: its order is what the screen shows.
        for criterion in items:
            groups.setdefault(_scope(criterion), ([], []))[side].append(criterion)
    changes, unchanged = [], 0
    for (target, field), (new, old) in groups.items():
        new, old = list(new), list(old)
        for criterion in list(new):
            same = next((item for item in old if _value(item) == _value(criterion)), None)
            if same is not None:
                old.remove(same)
                new.remove(criterion)
                unchanged += 1
        scope = {'target': target or None, 'field': field if target else None}
        if len(old) == 1 and len(new) == 1:
            changes.append(scope | {'change': 'changed', 'label': _label(new[0], old[0]),
                                    'before': _shown(old[0]), 'after': _shown(new[0])})
            continue
        changes += [scope | {'change': 'added', 'label': _label(item), 'before': None, 'after': _shown(item)}
                    for item in new]
        changes += [scope | {'change': 'removed', 'label': _label(item), 'before': _shown(item), 'after': None}
                    for item in old]
    return {'changes': changes, 'unchanged': unchanged,
            'counts': dict(Counter(item['change'] for item in changes))}


def pick_previous(candidates, document_id, document_version_id, version_no):
    """(row, basis) from approved criteria ordered newest approval first; (None, None) if there is none."""
    revisions = [row for row in candidates
                 if row['document_id'] == document_id and row['document_version_no'] < version_no]
    if revisions:
        return max(revisions, key=lambda row: (row['document_version_no'], row['approved_at'])), 'document_revision'
    others = [row for row in candidates if row['document_version_id'] != document_version_id]
    return (others[0], 'project_previous') if others else (None, None)


def candidates_statement(project_id, run_id, before):
    """Criteria approved in this project before this run was created, newest approval first."""
    return (select(versions, bindings.c.approval_id, approvals.c.approved_at,
                   documents.c.version_no.label('document_version_no'))
            .select_from(versions
                         .join(bindings, (bindings.c.criteria_version_id == versions.c.criteria_version_id)
                               & (bindings.c.project_id == versions.c.project_id))
                         .join(approvals, approvals.c.approval_id == bindings.c.approval_id)
                         .join(documents, (documents.c.document_version_id == versions.c.document_version_id)
                               & (documents.c.project_id == versions.c.project_id)))
            .where(versions.c.project_id == project_id, versions.c.source_run_id != run_id,
                   approvals.c.approved_at < before)
            .order_by(approvals.c.approved_at.desc(), versions.c.criteria_version_id).limit(CANDIDATES))


def reviews_statement(project_id, version_id: str):
    """Poster review runs applying a criteria version: the graph's (run)-applies->(version), (run)-inspects->(poster)."""
    return (select(runs.c.run_id, runs.c.parent_run_id, runs.c.input_json, runs.c.snapshot_json, runs.c.created_at)
            .where(runs.c.project_id == project_id,
                   or_(runs.c.input_json[('baseline', 'criteria_version_id')].astext == version_id,
                       runs.c.snapshot_json['approved_criteria_version_id'].astext == version_id),
                   runs.c.input_json['asset'].astext.is_not(None))  # ->> is NULL for a JSON null and a missing key
            .order_by(runs.c.created_at, runs.c.run_id).limit(RUN_LIMIT + 1))


def summarize(affected: list[dict]) -> dict:
    return {'affected_runs': len(affected),
            'approved': sum(run['human_decision'] in APPROVED for run in affected),
            'by_status': dict(sorted(Counter(run['execution_status'] for run in affected).items()))}


class CriteriaChangeImpact:
    def __init__(self, review, graph=None):
        self.review, self.engine = review, review.engine
        self.graph = graph  # graph_store.FrameworkGraph or None (PostgreSQL then answers alone)

    # --- PostgreSQL reads, kept small so the flow is testable without a database ---

    def _current(self, c, subject, run_id) -> dict:
        row, snapshot = self.review._open(c, subject, run_id)
        latest = self.review._latest(c, run_id)
        record = self.review._record(latest) if latest is not None else None
        document = c.execute(select(documents.c.document_id, documents.c.version_no).where(
            documents.c.project_id == row['project_id'],
            documents.c.document_version_id == UUID(snapshot.context.document_version_id))).mappings().one()
        return {'project_id': row['project_id'], 'created_at': row['created_at'],
                'document_id': document['document_id'], 'document_version_no': document['version_no'],
                'document_version_id': UUID(snapshot.context.document_version_id),
                'criteria': record.version.criteria if record else snapshot.criteria.criteria,
                'criteria_version_id': str(record.version.criteria_version_id) if record else None,
                'version_no': record.version.version_no if record else None}

    def _previous(self, c, run_id, current) -> dict | None:
        rows = c.execute(candidates_statement(current['project_id'], run_id, current['created_at'])).mappings().all()
        row, basis = pick_previous(rows, current['document_id'], current['document_version_id'],
                                   current['document_version_no'])
        if row is None:
            return None
        version = self.review._record(row).version  # Integrity-checked record, as the review screen reads it.
        return {'basis': basis, 'criteria': version.criteria, 'criteria_version_id': str(version.criteria_version_id),
                'version_no': version.version_no, 'source_run_id': version.source_run_id,
                'document_version_id': str(row['document_version_id']), 'document_version_no': row['document_version_no'],
                'approval_id': str(row['approval_id']), 'approved_at': row['approved_at'].isoformat()}

    @staticmethod
    def _reviews(c, project_id, version_id: str) -> tuple[list[dict], list[dict], bool]:
        rows = c.execute(reviews_statement(project_id, version_id)).mappings().all()
        truncated, rows = len(rows) > RUN_LIMIT, rows[:RUN_LIMIT]
        ids = [row['run_id'] for row in rows]
        decided = c.execute(select(approvals.c.approval_id, approvals.c.run_id, approvals.c.decision)
                            .where(approvals.c.project_id == project_id, approvals.c.run_id.in_(ids))
                            .order_by(approvals.c.approved_at, approvals.c.approval_id)).mappings().all()
        records = [{'approval_id': str(row['approval_id']), 'run_id': row['run_id'], 'decision': row['decision']}
                   for row in decided]
        affected = []
        for row in rows:
            snapshot = RunSnapshot.model_validate(row['snapshot_json'])
            affected.append({
                'run_id': row['run_id'], 'parent_run_id': row['parent_run_id'],
                'asset_version_id': row['input_json']['asset']['asset_version_id'],
                'execution_status': snapshot.execution_status, 'machine_verdict': snapshot.machine_verdict,
                'human_decision': snapshot.human_decision, 'created_at': row['created_at'].isoformat(),
                'decisions': [record['decision'] for record in records if record['run_id'] == row['run_id']]})
        return affected, records, truncated

    # --- Neo4j step ---

    def _related(self, project_id, version_id: str, revision: int):
        """(graph state, warning codes, relation sets or None). None means PostgreSQL answers alone."""
        state = {'status': 'unavailable', 'graph_revision': None, 'postgres_revision': revision}
        if self.graph is None:
            return state, ['GRAPH_NOT_CONFIGURED'], None
        if not revision:
            return state, ['GRAPH_NOT_PROJECTED'], None
        try:
            found = self.graph.review_impact(str(project_id), version_id)
        except GraphUnavailable as error:
            return state, [error.code], None
        state['graph_revision'] = found['revision']
        if found['revision'] != revision:
            return state | {'status': 'stale'}, ['GRAPH_STALE'], None
        if found['truncated']:
            return state | {'status': 'stale'}, ['GRAPH_RESULT_LIMIT'], None
        return state | {'status': 'complete'}, [], (
            {(run['run_id'], run['asset_version_id']) for run in found['runs']},
            {(approval['approval_id'], approval['run_id']) for approval in found['approvals']})

    def read(self, subject, run_id: str) -> dict:
        with unit_of_work(self.engine) as c:
            current = self._current(c, subject, run_id)
            previous = self._previous(c, run_id, current)
            revision = postgres_revision(c, current['project_id']) if previous else 0
        answer = {'run_id': run_id,
                  'current': {'document_version_id': str(current['document_version_id']),
                              'criteria_version_id': current['criteria_version_id'],
                              'version_no': current['version_no'], 'criteria': len(current['criteria'])}}
        if previous is None:
            return answer | {'previous': None, 'changes': [], 'unchanged': 0, 'counts': {}, 'affected_runs': [],
                             'summary': summarize([]), 'truncated': False, 'answered_by': 'postgres',
                             'graph': None, 'warnings': []}
        version_id = previous['criteria_version_id']
        graph, warnings, related = self._related(current['project_id'], version_id, revision)
        with unit_of_work(self.engine) as c:
            self.review._open(c, subject, run_id)  # Access is rechecked after the graph call.
            affected, records, truncated = self._reviews(c, current['project_id'], version_id)
        if related is not None and truncated:
            warnings, related = ['RESULT_LIMIT'], None  # A cut list cannot be compared row for row.
        elif related is not None and related != (
                {(run['run_id'], run['asset_version_id']) for run in affected},
                {(record['approval_id'], record['run_id']) for record in records}):
            graph, warnings, related = graph | {'status': 'stale'}, ['GRAPH_RELATION_MISMATCH'], None
        return answer | compare_criteria(previous['criteria'], current['criteria']) | {
            'previous': {key: value for key, value in previous.items() if key != 'criteria'},
            'affected_runs': affected, 'summary': summarize(affected), 'truncated': truncated,
            'answered_by': 'neo4j' if related is not None else 'postgres_fallback',
            'graph': graph, 'warnings': warnings}
