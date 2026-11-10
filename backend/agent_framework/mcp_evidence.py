"""Read-only evidence for the MCP tools: approved criteria, history, sources, relations.

Each call acts as the run requester and rechecks that requester's current
access the same way the other MCP tools do (McpStore). Queries are fixed SQL
over the existing AF001-AF004 tables (and fixed Cypher for relations); nothing
is written. A poster review run answers with the criteria run its baseline
pinned.

verify_sources compares stored records with each other: the A02 step output,
its extracted text blocks and the registered document hashes. It does not
reread PDF bytes; criteria approval and poster review already do that.

get_relations and get_change_impact go PostgreSQL -> Neo4j -> PostgreSQL:
access and the required revision first, the team's projection only when it has
that same revision, then the access and every returned row are checked again.
When the graph is missing, stale or down, the same answer is computed from
PostgreSQL and marked answered_by='postgres_fallback'.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select

from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from .contracts import RunSnapshot
from .criteria_review import PostgresCriteriaReview, bindings, versions
from .documents import versions as documents
from .mcp_graph import (criteria_node, document_node, impact_from, load_graph, postgres_revision,
                        read_current, relations_from, run_node)
from .mcp_postgres import RunActor, _refusals
from .postgres import PostgresStore, approvals, digest, payload, runs, steps

LIMIT = 50  # Rows per history list; `*_truncated` says when more exist.
ANSWERED_BY = 'postgres'


def _quote(source):
    return {'page': source.page, 'block_id': source.block_id, 'quote': source.quote}


def _value(value):
    return value and {'reference': value.reference, 'quote': value.quote}


def _edit(edit):
    return {'criterion_id': edit.criterion_id, 'action': edit.action, 'reason': edit.reason,
            'reference': edit.source.reference}


def _limited(rows):
    rows = list(rows)
    return rows[:LIMIT], len(rows) > LIMIT


def source_status(checks: dict, items: list, latest: bool) -> str:
    """A broken record outranks a newer document version: mismatch > stale > verified."""
    if not all(checks.values()) or any(item['status'] != 'verified' for item in items):
        return 'mismatch'
    return 'verified' if latest else 'stale'


class PostgresEvidence:
    """Evidence backend for FrameworkTools over a McpStore."""

    def __init__(self, store: PostgresStore, graph=None):
        self.store = store
        self.criteria = PostgresCriteriaReview(store)
        self.graph = graph  # graph_store.FrameworkGraph or None (relations then come from PostgreSQL)

    def _asked(self, c, run_id):
        """The asked run with its requester's current access rechecked."""
        asked = self.store._row(c, run_id)
        subject = RunActor(str(asked['actor_id']))
        snapshot = RunSnapshot.model_validate(asked['snapshot_json'])
        self.store._access(c, subject, snapshot.context, write=False)
        return asked, subject, snapshot

    def _open(self, c, run_id):
        """Requester-checked source criteria run; a poster run resolves to its pinned baseline."""
        asked, subject, snapshot = self._asked(c, run_id)
        baseline = snapshot.baseline
        row, source = self.criteria._open(c, subject, baseline.source_run_id if baseline else run_id)
        if row['project_id'] != asked['project_id']:
            raise AuthError('POSTER_BASELINE_MISMATCH', 409)
        return baseline, row, source

    def _approved(self, c, baseline, source_id):
        try:
            record = self.criteria._approved(c, source_id)
        except AuthError as error:
            if error.code == 'RESOURCE_NOT_FOUND':
                raise AuthError('CRITERIA_NOT_APPROVED', 409) from None
            raise
        if baseline is not None and (baseline.approval_id != str(record.approval_id)
                                     or baseline.criteria_version_id != str(record.criteria.version.criteria_version_id)
                                     or baseline.criteria_sha256 != record.criteria.sha256):
            raise AuthError('POSTER_BASELINE_MISMATCH', 409)
        return record

    @_refusals
    def approved_criteria(self, run_id: str) -> dict:
        with unit_of_work(self.store.engine) as c:
            baseline, row, _source = self._open(c, run_id)
            record = self._approved(c, baseline, row['run_id'])
        version = record.criteria.version
        return {
            'run_id': run_id, 'source_run_id': row['run_id'], 'answered_by': ANSWERED_BY,
            'criteria_version_id': str(version.criteria_version_id), 'version_no': version.version_no,
            'criteria_sha256': record.criteria.sha256, 'document_version_id': version.context.document_version_id,
            'approval_id': str(record.approval_id), 'approved_at': record.approved_at.isoformat(),
            'approved_by': record.approval.actor_id, 'purpose': record.purpose, 'synthetic': record.synthetic,
            'criteria': [{'criterion_id': c.criterion_id, 'text': c.text, 'kind': c.kind, 'operator': c.operator,
                          'expected': c.expected, 'unit': c.unit, 'required': c.required, 'human_added': c.human_added,
                          'source': _quote(c.source), 'value_source': _value(c.value_source)} for c in version.criteria],
            'exclusions': [_edit(edit) for edit in version.exclusions],
            'frozen_evidence': {'status': record.evidence.status, 'context_id': record.evidence.context_id,
                                'items': len(record.evidence.evidence), 'warnings': list(record.evidence.warnings)},
        }

    @_refusals
    def history(self, run_id: str) -> dict:
        with unit_of_work(self.store.engine) as c:
            _baseline, row, source = self._open(c, run_id)
            source_id, pid = row['run_id'], row['project_id']
            approved_id = c.execute(select(bindings.c.criteria_version_id)
                                    .where(bindings.c.source_run_id == source_id)).scalar_one_or_none()
            records = [self.criteria._record(r) for r in c.execute(
                select(versions).where(versions.c.source_run_id == source_id).order_by(versions.c.version_no)).mappings()]
            document_ids = self._document_ids(c, pid, source.context.document_version_id)
            docs, docs_more = _limited(c.execute(
                select(documents).where(documents.c.project_id == pid, documents.c.document_id.in_(document_ids))
                .order_by(documents.c.version_no.desc()).limit(LIMIT + 1)).mappings())
            # Poster review runs pin their criteria run in the fixed input; there is no index on it yet.
            posters, posters_more = _limited(c.execute(
                select(runs.c.run_id, runs.c.parent_run_id, runs.c.snapshot_json, runs.c.created_at)
                .where(runs.c.project_id == pid, runs.c.input_json[('baseline', 'source_run_id')].astext == source_id)
                .order_by(runs.c.created_at, runs.c.run_id).limit(LIMIT + 1)).mappings())
            run_ids = [source_id] + [p['run_id'] for p in posters]
            decisions, decisions_more = _limited(c.execute(
                select(approvals).where(approvals.c.project_id == pid, approvals.c.run_id.in_(run_ids))
                .order_by(approvals.c.approved_at, approvals.c.approval_id).limit(LIMIT + 1)).mappings())
        latest = docs[0]['document_version_id'] if docs else None
        return {
            'run_id': run_id, 'source_run_id': source_id, 'answered_by': ANSWERED_BY,
            'criteria_versions': [{
                'criteria_version_id': str(r.version.criteria_version_id), 'version_no': r.version.version_no,
                'parent_version_id': r.version.parent_version_id and str(r.version.parent_version_id),
                'created_at': r.version.created_at.isoformat(), 'created_by': r.version.created_by,
                'purpose': r.version.purpose, 'synthetic': r.version.synthetic,
                'approved': r.version.criteria_version_id == approved_id,
                'unresolved_criterion_ids': sorted(r.approval_blockers),
                'changes': [_edit(edit) for edit in r.version.changes]} for r in records],
            'documents': [{
                'document_version_id': str(d['document_version_id']), 'version_no': d['version_no'],
                'previous_version_id': d['previous_version_id'] and str(d['previous_version_id']),
                'original_name': d['original_name'], 'media_type': d['media_type'], 'page_count': d['page_count'],
                'created_at': d['created_at'].isoformat(),
                'used_by_criteria': str(d['document_version_id']) == source.context.document_version_id,
                'latest': d['document_version_id'] == latest} for d in docs],
            'documents_truncated': docs_more,
            'poster_reviews': [self._poster(p) for p in posters], 'poster_reviews_truncated': posters_more,
            'approvals': [{'run_id': a['run_id'], 'approval_id': str(a['approval_id']), 'decision': a['decision'],
                           'approved_at': a['approved_at'].isoformat(), 'actor_id': str(a['actor_id']),
                           'reason': a['payload_json'].get('reason')} for a in decisions],
            'approvals_truncated': decisions_more,
        }

    @staticmethod
    def _document_ids(c, pid, document_version_id):
        try:
            version_id = UUID(document_version_id)
        except ValueError:
            return []
        found = c.execute(select(documents.c.document_id).where(
            documents.c.project_id == pid, documents.c.document_version_id == version_id)).scalar_one_or_none()
        return [found] if found is not None else []

    @staticmethod
    def _poster(row):
        snapshot = RunSnapshot.model_validate(row['snapshot_json'])
        return {'run_id': row['run_id'], 'parent_run_id': row['parent_run_id'],
                'asset_version_id': snapshot.context.asset_version_id, 'execution_status': snapshot.execution_status,
                'machine_verdict': snapshot.machine_verdict, 'human_decision': snapshot.human_decision,
                'created_at': row['created_at'].isoformat()}

    @_refusals
    def verify_sources(self, run_id: str) -> dict:
        with unit_of_work(self.store.engine) as c:
            baseline, row, source = self._open(c, run_id)
            record = self._approved(c, baseline, row['run_id'])
            step = c.execute(select(steps).where(steps.c.run_id == row['run_id'], steps.c.role == 'A02')).mappings().one_or_none()
            document = self._document(c, row['project_id'], record.criteria.version.context.document_version_id)
        version, trace = record.criteria.version, source.criteria.extraction
        latest = document is not None and document['latest']
        checks = {
            'a02_output_intact': (step is not None and step['state'] == 'completed'
                                  and step['output_sha256'] == digest(source.criteria)
                                  and step['output_json'] == payload(source.criteria)),
            'document_registered': document is not None,
            'document_hash_matches': (document is not None and document['sha256'] == version.document_sha256
                                      == trace.document_sha256),
        }
        blocks = {(block.page, block.block_id): block for block in trace.sources}
        items = [self._check(criterion, blocks) for criterion in version.criteria]
        return {
            'run_id': run_id, 'source_run_id': row['run_id'], 'answered_by': ANSWERED_BY,
            'status': source_status(checks, items, latest),
            'criteria_version_id': str(version.criteria_version_id), 'document_version_id': version.context.document_version_id,
            'document_is_latest': latest, 'checks': checks, 'criteria': items,
            'scope': 'Stored A02 text blocks and database hashes; PDF bytes are not reread.',
        }

    @staticmethod
    def _document(c, pid, document_version_id):
        try:
            version_id = UUID(document_version_id)
        except ValueError:
            return None
        doc = c.execute(select(documents.c.document_id, documents.c.sha256).where(
            documents.c.project_id == pid, documents.c.document_version_id == version_id)).mappings().one_or_none()
        if doc is None:
            return None
        newest = c.execute(select(documents.c.document_version_id).where(
            documents.c.project_id == pid, documents.c.document_id == doc['document_id'])
            .order_by(documents.c.version_no.desc()).limit(1)).scalar_one()
        return {'sha256': doc['sha256'], 'latest': newest == version_id}

    @staticmethod
    def _check(criterion, blocks):
        source = criterion.source
        block = blocks.get((source.page, source.block_id))
        checks = {
            'quote_hash_matches': sha256(source.quote.encode()).hexdigest() == source.source_hash,
            'block_found': block is not None,
            'block_text_intact': block is not None and sha256(block.text.encode()).hexdigest() == block.text_sha256,
            'quote_in_block': block is not None and source.quote in block.text,
        }
        value = criterion.value_source
        if value is not None:
            # Human-supplied values are grounded in their own quote, not the document block.
            checks['value_source_grounded'] = (sha256(value.quote.encode()).hexdigest() == value.quote_sha256
                                               and criterion.expected in value.quote)
        return {'criterion_id': criterion.criterion_id, 'page': source.page, 'block_id': source.block_id,
                'status': 'verified' if all(checks.values()) else 'mismatch', 'checks': checks}

    # --- Relations: PostgreSQL -> Neo4j -> PostgreSQL ---

    def _projection(self, project_id, revision, needed):
        """Neo4j step: (graph state, warnings, Graph); no Graph means PostgreSQL must answer."""
        state, warnings, projected = read_current(self.graph, project_id, revision)
        if projected is not None and needed not in projected.nodes:
            return state | {'status': 'stale'}, ['GRAPH_NODE_MISSING'], None
        return state, warnings, projected

    @staticmethod
    def _absent(c, project_id, node_ids) -> bool:
        """PostgreSQL recheck: does any returned node lack its row in this project?"""
        tables = {'run': (runs.c.run_id, str), 'criteria': (versions.c.criteria_version_id, UUID),
                  'document': (documents.c.document_version_id, UUID), 'approval': (approvals.c.approval_id, UUID)}
        groups = {}
        for node in node_ids:
            kind, _, key = node.partition(':')
            groups.setdefault(kind, set()).add(key)
        for kind, keys in groups.items():
            if kind in ('poster', 'criterion', 'source'):
                continue  # These come from a run's fixed input or a criteria snapshot, rechecked with those rows.
            if kind not in tables:
                return True
            column, convert = tables[kind]
            try:
                wanted = {convert(key) for key in keys}
            except ValueError:
                return True
            found = set(c.execute(select(column).where(column.table.c.project_id == project_id,
                                                       column.in_(wanted))).scalars())
            if found != wanted:
                return True
        return False

    @_refusals
    def relations(self, run_id: str) -> dict:
        with unit_of_work(self.store.engine) as c:
            asked, _subject, _snapshot = self._asked(c, run_id)
            project_id = asked['project_id']
            revision = postgres_revision(c, project_id)
        start = run_node(run_id)
        graph, warnings, projected = self._projection(project_id, revision, start)
        answer = relations_from(projected, start) if projected is not None else None
        with unit_of_work(self.store.engine) as c:
            self._asked(c, run_id)
            if answer is not None and self._absent(c, project_id, [n['id'] for n in answer['nodes']]):
                graph, warnings, answer = graph | {'status': 'stale'}, ['GRAPH_REFERS_TO_ABSENT_ROWS'], None
            answered_by = 'neo4j' if answer is not None else 'postgres_fallback'
            if answer is None:
                answer = relations_from(load_graph(c, project_id), start)
        return {'run_id': run_id, 'answered_by': answered_by, 'graph': graph, 'warnings': warnings} | answer

    @staticmethod
    def _impact_rows(c, project_id, pinned, newer):
        """Current PostgreSQL facts for an impact answer; `complete` is False if any row is absent."""
        run_ids = [node.partition(':')[2] for node in pinned]
        try:
            document_ids = [UUID(node.partition(':')[2]) for node in newer]
        except ValueError:
            return [], [], False
        found = c.execute(select(runs.c.run_id, runs.c.parent_run_id, runs.c.snapshot_json, runs.c.created_at)
                          .where(runs.c.project_id == project_id, runs.c.run_id.in_(run_ids))
                          .order_by(runs.c.created_at, runs.c.run_id)).mappings().all()
        decided = {}
        for row in c.execute(select(approvals.c.run_id, approvals.c.decision)
                             .where(approvals.c.project_id == project_id, approvals.c.run_id.in_(run_ids))
                             .order_by(approvals.c.approved_at, approvals.c.approval_id)).mappings():
            decided.setdefault(row['run_id'], []).append(row['decision'])
        docs = c.execute(select(documents.c.document_version_id, documents.c.version_no, documents.c.created_at)
                         .where(documents.c.project_id == project_id, documents.c.document_version_id.in_(document_ids))
                         .order_by(documents.c.version_no.desc())).mappings().all()
        affected = [PostgresEvidence._poster(row) | {'decisions': decided.get(row['run_id'], [])} for row in found]
        newer_docs = [{'document_version_id': str(d['document_version_id']), 'version_no': d['version_no'],
                       'created_at': d['created_at'].isoformat()} for d in docs]
        return affected, newer_docs, len(found) == len(run_ids) and len(docs) == len(document_ids)

    @_refusals
    def change_impact(self, run_id: str) -> dict:
        with unit_of_work(self.store.engine) as c:
            baseline, row, _source = self._open(c, run_id)
            version = self._approved(c, baseline, row['run_id']).criteria.version
            project_id = row['project_id']
            revision = postgres_revision(c, project_id)
        criteria = criteria_node(version.criteria_version_id)
        document = document_node(version.context.document_version_id)
        source_run = run_node(row['run_id'])

        def reviews_of(graph_view):
            # The criteria run also applies its own approved version; only later reviews are affected.
            applying, revised = impact_from(graph_view, criteria, document)
            return [node for node in applying if node != source_run], revised

        graph, warnings, projected = self._projection(project_id, revision, criteria)
        answer = reviews_of(projected) if projected is not None else None
        with unit_of_work(self.store.engine) as c:
            self._open(c, run_id)
            if answer is not None:
                affected, newer, complete = self._impact_rows(c, project_id, *answer)
                if not complete:
                    graph, warnings, answer = graph | {'status': 'stale'}, ['GRAPH_REFERS_TO_ABSENT_ROWS'], None
            answered_by = 'neo4j' if answer is not None else 'postgres_fallback'
            if answer is None:
                affected, newer, _complete = self._impact_rows(c, project_id, *reviews_of(load_graph(c, project_id)))
        return {
            'run_id': run_id, 'source_run_id': row['run_id'], 'answered_by': answered_by, 'graph': graph,
            'warnings': warnings, 'criteria_version_id': str(version.criteria_version_id),
            'document_version_id': version.context.document_version_id,
            'document_changed': bool(newer), 'newer_document_versions': newer, 'affected_runs': affected,
            'summary': {'affected_runs': len(affected),
                        'by_status': dict(sorted(Counter(r['execution_status'] for r in affected).items())),
                        'final_or_exception_approved': sum(r['human_decision'] in ('final_approved', 'exception_approved')
                                                           for r in affected)},
        }
