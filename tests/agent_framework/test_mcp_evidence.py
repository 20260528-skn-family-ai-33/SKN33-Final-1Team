"""Evidence MCP tools without a database: requester checks, baseline pinning, source checks.

The SQL paths (history, verify_sources queries) are covered by db_integration.
Criteria come from the recorded synthetic A02 fixture used by test_criteria_review.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from backend.agent_framework import mcp_evidence
from backend.agent_framework.adapters.synthetic import make_agents
from backend.agent_framework.context import freeze_approved_context
from backend.agent_framework.contracts import Approval, ApprovedBaseline, RunSnapshot
from backend.agent_framework.criteria_contracts import CriteriaApprovalRecord, CriteriaVersionRecord
from backend.agent_framework.criteria_review import approval_blockers
from backend.agent_framework.mcp_evidence import PostgresEvidence, source_status
from backend.agent_framework.mcp_postgres import RunActor
from backend.agent_framework.mcp_server import EVIDENCE_TOOL_NAMES, FrameworkTools
from backend.agent_framework.postgres import digest
from backend.agent_framework.runtime import Workflow
from backend.app.security.errors import AuthError
from tests.agent_framework.test_criteria_review import draft, version

PROJECT = UUID('00000000-0000-4000-8000-000000000001')
OTHER_PROJECT = UUID('00000000-0000-4000-8000-000000000002')
REQUESTER = '00000000-0000-4000-8000-0000000000a1'
POSTER = 'poster-run-1'


class Store:
    """Rows by run ID; records each access check and refuses when told to."""
    engine = None

    def __init__(self, rows, error=None):
        self.rows, self.error, self.access = rows, error, []

    def _row(self, c, run_id):
        if run_id not in self.rows:
            raise AuthError('FRAMEWORK_RUN_NOT_FOUND', 404)
        return self.rows[run_id]

    def _access(self, c, subject, context, *, write=True):
        self.access.append((subject, context.run_id, write))
        if self.error:
            raise self.error


class Criteria:
    """Stands in for PostgresCriteriaReview: same open rule, a fixed approval record."""

    def __init__(self, store, record):
        self.store, self.record = store, record

    def _open(self, c, subject, run_id, *, write=False):
        row = self.store._row(c, run_id)
        snapshot = RunSnapshot.model_validate(row['snapshot_json'])
        self.store._access(c, subject, snapshot.context, write=write)
        return row, snapshot

    def _approved(self, c, run_id):
        if self.record is None:
            raise AuthError('RESOURCE_NOT_FOUND', 404)
        return self.record


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    @contextmanager
    def unit_of_work(engine):
        yield None
    monkeypatch.setattr(mcp_evidence, 'unit_of_work', unit_of_work)


def approved(v):
    record = CriteriaVersionRecord(version=v, sha256=digest(v), approval_blockers=approval_blockers(v.criteria))
    approval = Approval(context=v.context, actor_id=REQUESTER, decision='approve_criteria', reason='개발용 확인',
                        criterion_ids=tuple(c.criterion_id for c in v.criteria),
                        approved_criteria_version_id=str(v.criteria_version_id), criteria_sha256=record.sha256)
    return CriteriaApprovalRecord(approval_id=uuid4(), approval=approval, approved_at=datetime.now(timezone.utc),
                                  criteria=record, evidence=freeze_approved_context(v.context, v.criteria, synthetic=True),
                                  purpose='synthetic_test', synthetic=True)


def pin(record, **changes):
    v = record.criteria.version
    return ApprovedBaseline(**{'source_run_id': v.source_run_id, 'criteria_version_id': str(v.criteria_version_id),
                               'criteria_sha256': record.criteria.sha256, 'approval_id': str(record.approval_id),
                               'document_sha256': v.document_sha256} | changes)


def row(run_id, snapshot, project=PROJECT):
    return {'run_id': run_id, 'actor_id': UUID(REQUESTER), 'project_id': project,
            'snapshot_json': snapshot.model_dump(mode='json')}


def setup(*, record=True, baseline=None, poster_project=PROJECT, error=None):
    source = draft()
    v = version(source)
    record = approved(v) if record else None
    snapshot = RunSnapshot(context=source.context, profile='test', synthetic=True, execution_status='waiting_asset',
                           criteria=source)
    rows = {source.context.run_id: row(source.context.run_id, snapshot)}
    if record is not None:
        poster = RunSnapshot(context=source.context.model_copy(update={'run_id': POSTER}), profile='test',
                             synthetic=True, execution_status='completed',
                             baseline=baseline(record) if baseline else pin(record))
        rows[POSTER] = row(POSTER, poster, poster_project)
    store = Store(rows, error)
    evidence = PostgresEvidence(store)
    evidence.criteria = Criteria(store, record)
    return FrameworkTools(Workflow(make_agents()), evidence), store, source, v


def test_evidence_tools_are_exposed_only_with_an_evidence_backend():
    memory = FrameworkTools(Workflow(make_agents()))
    assert memory.call('get_history', {'run_id': 'demo-run-1'})['error']['code'] == 'UNKNOWN_TOOL'
    tools, *_ = setup()
    assert tools.names[-5:] == EVIDENCE_TOOL_NAMES == ('get_approved_criteria', 'get_history', 'verify_sources',
                                                       'get_relations', 'get_change_impact')


def test_approved_criteria_act_as_the_requester_and_carry_source_quotes():
    tools, store, source, v = setup()
    run_id = source.context.run_id
    receipt = tools.call('get_approved_criteria', {'run_id': run_id})
    result = receipt['result']
    assert receipt['state'] == 'completed' and result['source_run_id'] == run_id and result['answered_by'] == 'postgres'
    assert [c['source']['quote'] for c in result['criteria']] == [c.source.quote for c in v.criteria]
    # The fixture amends the first criterion with a declared value and excludes the rest.
    assert result['criteria'][0]['value_source']['reference'] == 'synthetic:test-only'
    assert [e['criterion_id'] for e in result['exclusions']] == [c.criterion_id for c in source.criteria[1:]]
    assert result['frozen_evidence']['status'] == 'complete' and result['synthetic'] is True
    assert store.access == [(RunActor(REQUESTER), run_id, False)] * 2


def test_poster_review_answers_with_its_pinned_criteria_run():
    tools, store, source, v = setup()
    result = tools.call('get_approved_criteria', {'run_id': POSTER})['result']
    assert result['run_id'] == POSTER and result['source_run_id'] == source.context.run_id
    assert result['criteria_version_id'] == str(v.criteria_version_id)
    assert store.access == [(RunActor(REQUESTER), POSTER, False), (RunActor(REQUESTER), source.context.run_id, False)]


@pytest.mark.parametrize('name', ['get_approved_criteria', 'verify_sources'])
@pytest.mark.parametrize('changes', [{'baseline': lambda r: pin(r, criteria_sha256='0' * 64)},
                                     {'baseline': lambda r: pin(r, approval_id=str(uuid4()))},
                                     {'poster_project': OTHER_PROJECT}])
def test_poster_runs_that_do_not_match_their_pin_are_refused(name, changes):
    tools, *_ = setup(**changes)
    assert tools.call(name, {'run_id': POSTER})['error']['code'] == 'POSTER_BASELINE_MISMATCH'


@pytest.mark.parametrize('name', ['get_approved_criteria', 'verify_sources'])
def test_unapproved_criteria_are_a_refusal_not_an_empty_answer(name):
    tools, _, source, _ = setup(record=False)
    assert tools.call(name, {'run_id': source.context.run_id})['error'] == {
        'code': 'CRITERIA_NOT_APPROVED', 'message': 'The criteria run has no approved criteria version yet.'}


@pytest.mark.parametrize('name', EVIDENCE_TOOL_NAMES)
def test_revoked_access_and_unknown_runs_are_refused_before_any_query(name):
    tools, _, source, _ = setup(error=AuthError('FRAMEWORK_ACCESS_REVOKED', 403))
    assert tools.call(name, {'run_id': source.context.run_id})['error']['code'] == 'FRAMEWORK_ACCESS_REVOKED'
    assert tools.call(name, {'run_id': 'missing'})['error']['code'] == 'RUN_NOT_FOUND'


def blocks(source):
    return {(b.page, b.block_id): b for b in source.extraction.sources}


def test_every_recorded_quote_is_found_in_its_stored_a02_block():
    source = draft()
    items = [PostgresEvidence._check(c, blocks(source)) for c in source.criteria]
    assert items and all(item['status'] == 'verified' for item in items)
    assert all('value_source_grounded' not in item['checks'] for item in items)


def test_changed_or_missing_blocks_are_mismatches():
    source = draft()
    criterion = source.criteria[0]
    found = blocks(source)
    key = (criterion.source.page, criterion.source.block_id)
    changed = found | {key: found[key].model_copy(update={'text': '다른 원문'})}
    checks = PostgresEvidence._check(criterion, changed)['checks']
    assert checks['block_found'] and not checks['block_text_intact'] and not checks['quote_in_block']
    missing = {k: b for k, b in found.items() if k != key}
    assert PostgresEvidence._check(criterion, missing) == {
        'criterion_id': criterion.criterion_id, 'page': key[0], 'block_id': key[1], 'status': 'mismatch',
        'checks': {'quote_hash_matches': True, 'block_found': False, 'block_text_intact': False, 'quote_in_block': False}}


def test_human_values_are_checked_against_their_own_quote():
    source = draft()
    amended = version(source).criteria[0]
    assert PostgresEvidence._check(amended, blocks(source))['checks']['value_source_grounded'] is True
    forged = amended.model_copy(update={'value_source': amended.value_source.model_copy(update={'quote': '다른 회신'})})
    item = PostgresEvidence._check(forged, blocks(source))
    assert item['status'] == 'mismatch' and item['checks']['value_source_grounded'] is False


def test_a_broken_record_outranks_a_newer_document_version():
    ok, bad = {'status': 'verified'}, {'status': 'mismatch'}
    assert source_status({'a': True}, [ok], True) == 'verified'
    assert source_status({'a': True}, [ok], False) == 'stale'
    assert source_status({'a': False}, [ok], False) == 'mismatch'
    assert source_status({'a': True}, [ok, bad], True) == 'mismatch'
