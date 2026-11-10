"""Evidence MCP tools over a real PostgreSQL 17: PDF -> criteria approval -> poster reviews.

Run explicitly with -m db_integration and TEST_DB_ENV_FILE. Reuses the poster
pipeline fixture: a registered sample PDF, scripted A02, a development-only
criteria approval and scripted poster observations (not Qwen, not a client
decision). The fixture writes real storage roots, so it needs a POSIX host.
"""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, update

from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database, csrf, email_user  # noqa: F401
from tests.app.auth_fixtures import auth_environment  # noqa: F401
from tests.agent_framework.test_pdf_postgres_integration import (  # noqa: F401
    document_database as initial_pdf_database, runtime, upload)
from tests.agent_framework.test_criteria_postgres_integration import document_database  # noqa: F401
from tests.agent_framework.test_a02_postgres_integration import a02  # noqa: F401
from tests.agent_framework.test_poster_postgres_integration import (  # noqa: F401
    approve, create_review, image, pipeline, post, review_body, run_worker)
from tests.agent_framework.test_agents_mcp import TOKEN, mcp_server
from backend.app.services.business import table
from backend.agent_framework.mcp_evidence import PostgresEvidence
from backend.agent_framework.mcp_postgres import McpStore, PostgresRuns
from backend.agent_framework.mcp_server import EVIDENCE_TOOL_NAMES, FrameworkTools, McpToolClient

pytestmark = pytest.mark.db_integration


def backends(r):
    store = McpStore(r.db.engine, r.config)
    return PostgresRuns(store), PostgresEvidence(store)


def answer(tools, name, run_id):
    receipt = tools.call(name, {'run_id': run_id})
    assert receipt['state'] == 'completed', receipt
    return receipt['result']


def test_evidence_follows_the_pinned_criteria_through_reviews_and_document_versions(pipeline):
    r = pipeline
    tools = FrameworkTools(*backends(r))
    version = r.baseline['version']
    approved = answer(tools, 'get_approved_criteria', r.source_id)
    assert approved['criteria_version_id'] == version['criteria_version_id']
    assert approved['criteria_sha256'] == r.baseline['sha256'] and approved['approval_id'] == r.approved['approval_id']
    assert [c['source']['quote'] for c in approved['criteria']] == [c['source']['quote'] for c in version['criteria']]
    assert [e['criterion_id'] for e in approved['exclusions']] == [e['criterion_id'] for e in version['exclusions']]
    assert approved['answered_by'] == 'postgres' and approved['synthetic'] is True

    # A failing review, its approved change request and the revised poster run.
    first = create_review(r, image(r, 'wrong_date'))
    assert approve(r, first, 'approve_criteria').status_code == 200
    failed, outcome, _ = run_worker(r)
    assert outcome == 'completed' and failed['machine_verdict'] == 'fail'
    assert approve(r, failed, 'approve_changes').status_code == 200
    failed_id = failed['context']['run_id']
    response = post(r.client, '/api/framework/v1/runs/' + failed_id + '/poster-revisions',
                    review_body(r, image(r, 'revised')))
    assert response.status_code == 201, response.text
    child = response.json()['context']['run_id']

    # A poster run answers with the criteria its baseline pinned.
    assert answer(tools, 'get_approved_criteria', child) == approved | {'run_id': child}
    history = answer(tools, 'get_history', child)
    assert history['source_run_id'] == r.source_id
    assert [(v['version_no'], v['approved']) for v in history['criteria_versions']] == [(1, True)]
    assert [(p['run_id'], p['parent_run_id']) for p in history['poster_reviews']] == [(failed_id, None), (child, failed_id)]
    assert [p['machine_verdict'] for p in history['poster_reviews']] == ['fail', None]
    assert [(a['run_id'], a['decision']) for a in history['approvals']] == [
        (r.source_id, 'approve_criteria'), (failed_id, 'approve_criteria'), (failed_id, 'approve_changes')]
    assert [(d['version_no'], d['latest'], d['used_by_criteria']) for d in history['documents']] == [(1, True, True)]
    assert not (history['documents_truncated'] or history['poster_reviews_truncated'] or history['approvals_truncated'])

    checked = answer(tools, 'verify_sources', child)
    assert checked['status'] == 'verified' and checked['document_is_latest'] and all(checked['checks'].values())
    assert [c['criterion_id'] for c in checked['criteria']] == [c['criterion_id'] for c in version['criteria']]
    assert all(c['status'] == 'verified' for c in checked['criteria'])

    # A newer document version makes the approved sources stale, not wrong.
    replaced = upload(r, r.source_raw, document=r.registered['document_id'],
                      previous=r.registered['document']['document_version_id'])
    assert replaced.status_code == 201, replaced.text
    stale = answer(tools, 'verify_sources', r.source_id)
    assert stale['status'] == 'stale' and not stale['document_is_latest'] and all(stale['checks'].values())
    documents = answer(tools, 'get_history', r.source_id)['documents']
    assert [(d['version_no'], d['latest'], d['used_by_criteria']) for d in documents] == [(2, True, False), (1, False, True)]


def test_evidence_tools_recheck_current_membership(pipeline):
    r = pipeline
    tools = FrameworkTools(*backends(r))
    assert answer(tools, 'get_history', r.source_id)['source_run_id'] == r.source_id
    members = table('project_members')
    replacement = email_user(r.auth_database)
    with r.owner.begin() as c:
        c.execute(insert(members).values(project_id=r.pid, user_id=UUID(replacement['id']), role='owner', is_active=True))
        c.execute(update(members).where(members.c.project_id == r.pid, members.c.user_id == UUID(str(r.subject.user_id)))
                  .values(role='viewer'))
    for name in EVIDENCE_TOOL_NAMES:
        assert tools.call(name, {'run_id': r.source_id})['error']['code'] == 'FRAMEWORK_ACCESS_REVOKED'
        assert tools.call(name, {'run_id': 'missing-' + uuid4().hex})['error']['code'] == 'RUN_NOT_FOUND'


def test_evidence_tools_are_served_over_http(pipeline):
    r = pipeline
    with mcp_server(*backends(r)) as url:
        client = McpToolClient(url, TOKEN, timeout=10)
        assert client.call('verify_sources', {'run_id': r.source_id})['result']['status'] == 'verified'
        assert client.call('get_approved_criteria', {'run_id': 'missing-' + uuid4().hex})['error']['code'] == 'RUN_NOT_FOUND'
