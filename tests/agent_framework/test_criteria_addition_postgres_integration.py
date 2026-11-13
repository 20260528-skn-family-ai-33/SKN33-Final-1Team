"""Exercise the text-only A02 addition through the real PostgreSQL worker."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from threading import Barrier
from uuid import uuid4

import pytest

from backend.agent_framework.contracts import Criterion
from backend.agent_framework.criteria_contracts import CriteriaAdditionRequest
from backend.agent_framework.mcp_evidence import PostgresEvidence
from backend.agent_framework.postgres import digest
from backend.agent_framework.postgres_schema import migrate
from backend.app.persistence.transaction import unit_of_work
from tests.agent_framework.test_a02_postgres_integration import a02, admit, worker
from tests.agent_framework.test_criteria_postgres_integration import (
    approval_body, create, document_database as initial_database, post, review, version_body,
)
from tests.agent_framework.test_criteria_review import edit
from tests.agent_framework.test_pdf_postgres_integration import document_database as initial_pdf_database, runtime
from tests.agent_framework.test_semantic_postgres_integration import brief_docx, register_docx
from tests.app.test_db_integration import database
from tests.app.test_v1_auth_integration import auth_database, csrf

pytestmark = pytest.mark.db_integration


@pytest.fixture(scope='module')
def document_database(initial_database):
    auth, migration = initial_database
    assert migrate(migration, target='AF004')['revision'] == 'AF004'
    return auth, migration


def addition_request(review, text):
    return {'parent_version_id': None, 'expected_base_sha256': digest(review.draft),
            'criterion_ids': [review.draft['criteria'][0]['criterion_id']], 'text': text}


def test_direct_input_is_extracted_once_and_saved_on_original_run(review):
    r = review
    keep = r.draft['criteria'][0]
    text = '포스터 제목에 여름을 표기한다.'
    request = addition_request(r, text)
    key = uuid4()
    queued = post(r, '/criteria-additions', request, key)
    assert queued.status_code == 202, queued.text
    addition_run_id = queued.json()['context']['run_id']
    assert addition_run_id != r.rid
    assert post(r, '/criteria-additions', request, key).json() == queued.json()
    assert post(r, '/criteria-additions', request | {'text': '다른 입력'}, key).status_code == 409
    assert r.client.get(r.route + '/criteria-review').json()['latest_version'] is None
    assert addition_run_id not in {item['run_id'] for item in r.client.get('/api/framework/v1/my-tasks').json()['items']}

    calls = []

    def exact_input(agents):
        def provider(sources):
            source, = sources
            calls.append(source.text)
            return json.dumps({'candidates': [{'text': '여름', 'quote': '여름', 'block_id': source.block_id, 'page': 1,
                'kind': 'text', 'operator': 'contains', 'expected': '여름', 'unit': None,
                'required': True, 'unresolved': False}]}).encode()
        agents.extractor.provider = provider

    outcome, claim, _queue = worker(r, change=exact_input)
    assert (outcome, claim.run_id) == ('completed', addition_run_id), r.client.get(
        '/api/framework/v1/runs/' + claim.run_id).json()['error']
    assert calls == [text]
    assert r.client.get(r.route).json()['criteria'] == r.draft
    saved = r.client.get(r.route + '/criteria-review').json()['latest_version']
    version = saved['version']
    assert version['source_run_id'] == r.rid
    assert version['source_draft_sha256'] == digest(r.draft)
    assert version['document_sha256'] == r.registered['document']['sha256']
    assert version['addition_run_ids'] == [addition_run_id]
    unchanged, added = version['criteria']
    assert unchanged == keep
    assert added['human_added'] and added['source']['kind'] == 'direct_input'
    assert added['source']['quote'] == text
    assert added['source']['source_hash'] == sha256(text.encode()).hexdigest()
    assert added['value_source']['reference'] == '담당자 직접 입력'
    assert version['changes'][-1]['action'] == 'add'
    assert len(r.client.get(r.route + '/criteria-versions').json()) == 1
    assert post(r, '/criteria-additions', request, key).json()['execution_status'] == 'waiting_criteria'

    saved = create(r, parent=saved)
    assert saved['version']['criteria'] == version['criteria']
    edits = [edit(keep['criterion_id'], expected='개발 테스트용 확인값')]
    resolved = post(r, '/criteria-versions', version_body(r, saved) | {'edits': edits}).json()
    approved = post(r, '/criteria-approvals', approval_body(resolved))
    assert approved.status_code == 200, approved.text
    with unit_of_work(r.db.engine) as connection:
        source_row = r.store._row(connection, r.rid)
        blocks = PostgresEvidence._addition_blocks(connection, source_row,
            r.review._approved(connection, r.rid).criteria.version)
    added = Criterion.model_validate(added)
    assert PostgresEvidence._check(added, blocks[added.source.document_version_id])['status'] == 'verified'


def test_adding_with_all_document_rows_preserves_unresolved_criteria(review):
    r = review
    request = addition_request(r, '제목에 봄을 표기한다.') | {'criterion_ids': None}
    queued = post(r, '/criteria-additions', request)
    assert queued.status_code == 202, queued.text
    assert worker(r)[0] == 'completed'
    latest = r.client.get(r.route + '/criteria-review').json()['latest_version']
    assert latest['version']['criteria'][:-1] == r.draft['criteria']
    assert latest['version']['exclusions'] == []


def test_docx_uses_its_existing_converted_source_when_adding_requirements(a02):
    r = a02
    original = register_docx(r, brief_docx(['제목에 여름을 표기한다.']))
    assert original.status_code == 201, original.text
    queued = admit(r, original.json())
    assert queued.status_code == 202, queued.text
    assert worker(r)[0] == 'completed'
    route = '/api/framework/v1/runs/' + queued.json()['context']['run_id']
    draft = r.client.get(route).json()['criteria']
    request = {'parent_version_id': None, 'expected_base_sha256': digest(draft), 'criterion_ids': None,
        'text': '부제에 전시를 표기한다.'}
    addition = r.client.post(route + '/criteria-additions', json=request,
        headers={**csrf(r.client), 'Idempotency-Key': str(uuid4())})
    assert addition.status_code == 202, addition.text
    assert worker(r)[0] == 'completed'
    version = r.client.get(route + '/criteria-review').json()['latest_version']['version']
    assert version['criteria'][:-1] == draft['criteria']
    assert version['document_sha256'] == original.json()['document']['sha256']
    assert version['criteria'][-1]['source']['quote'] == request['text']


def test_direct_input_does_not_overwrite_a_newer_criteria_version(review):
    r = review
    queued = post(r, '/criteria-additions', addition_request(r, '제목에 봄을 표기한다.'))
    assert queued.status_code == 202, queued.text
    latest = create(r)
    outcome, claim, _queue = worker(r)
    assert (outcome, claim.run_id) == ('failed', queued.json()['context']['run_id'])
    analysis = r.client.get('/api/framework/v1/runs/' + claim.run_id).json()
    assert analysis['error']['code'] == 'CRITERIA_VERSION_CONFLICT'
    assert r.client.get(r.route + '/criteria-versions').json() == [latest]


def test_concurrent_replays_share_one_analysis_job(review):
    r = review
    request = CriteriaAdditionRequest.model_validate(addition_request(r, '제목에 봄을 표기한다.'))
    key = uuid4()
    start = Barrier(2)

    def enqueue(_):
        start.wait(timeout=10)
        return r.review.start_addition(r.subject, r.rid, key, request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(enqueue, range(2))
    assert first == second
    outcome, claim, queue = worker(r)
    assert (outcome, claim.run_id) == ('completed', first.context.run_id)
    assert queue.claim_next() is None
    assert len(r.client.get(r.route + '/criteria-versions').json()) == 1
