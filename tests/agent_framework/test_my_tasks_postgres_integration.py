"""Queue/visibility permissions and races in a disposable PostgreSQL database."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, insert, update, func

from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database, csrf, email_user, login  # noqa: F401
from tests.agent_framework.test_postgres_integration import framework_database, runtime, start, work, approve  # noqa: F401
from backend.app.services.business import table
from backend.app.security.errors import AuthError
from backend.agent_framework.postgres import runs, jobs, steps, events, approvals
from backend.agent_framework.postgres_api import create_postgres_app
from backend.agent_framework.my_tasks import MyTasks

pytestmark = pytest.mark.db_integration
BASE = '/api/framework/v1'


def cancel(r):
    return r.client.post(BASE + '/runs/' + r.context.run_id + '/cancel', headers=csrf(r.client))


def visibility(r, hidden):
    return r.client.post(BASE + '/runs/' + r.context.run_id + '/history-visibility',
                         json={'hidden': hidden}, headers=csrf(r.client))


def rows(r):
    with r.db.engine.connect() as c:
        return {name: [dict(row) for row in c.execute(select(target).where(target.c.run_id == r.context.run_id)).mappings()]
                for name, target in [('runs', runs), ('jobs', jobs), ('steps', steps), ('approvals', approvals)]}


def test_cancel_preserves_input_is_idempotent_and_never_claimed(runtime):
    r = runtime; start(r); original = rows(r)
    assert r.client.get(BASE + '/my-tasks').json()['items'][0]['can_cancel']
    assert r.client.post(BASE + '/runs/' + r.context.run_id + '/cancel').status_code == 403
    reply = cancel(r); assert reply.status_code == 200, reply.text
    assert reply.json()['error']['code'] == 'USER_CANCELLED'
    assert cancel(r).json() == reply.json()
    assert r.queue.claim_next() is None
    saved = rows(r)
    assert saved['runs'][0]['input_json'] == original['runs'][0]['input_json']
    assert saved['runs'][0]['input_sha256'] == original['runs'][0]['input_sha256']
    assert saved['jobs'][0]['attempt_no'] == 0 and saved['jobs'][0]['state'] == 'failed'
    assert saved['steps'] == original['steps'] == []
    assert r.client.get(BASE + '/my-tasks').json()['items'][0]['state'] == 'cancelled'
    with r.db.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(events).where(events.c.run_id == r.context.run_id,
                         events.c.kind == 'job.cancelled')).scalar_one() == 1


def test_claimed_work_cannot_be_cancelled_or_hidden(runtime):
    r = runtime; start(r); claim = r.queue.claim_next(); assert claim
    original = rows(r)
    result = cancel(r); assert result.status_code == 409
    assert result.json()['error']['code'] == 'FRAMEWORK_TASK_NOT_QUEUED'
    assert visibility(r, True).status_code == 409
    assert rows(r) == original
    assert not r.client.get(BASE + '/my-tasks').json()['items'][0]['can_cancel']


@pytest.mark.parametrize('iteration', range(4))
def test_cancel_and_worker_claim_have_only_one_winner(runtime, iteration):
    r = runtime; start(r); barrier = Barrier(2)
    def cancellation():
        barrier.wait()
        try: MyTasks(r.store).cancel(r.subject, r.context.run_id); return 'cancelled'
        except AuthError as exc: return exc.code
    def claim():
        barrier.wait(); return r.queue.claim_next()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(cancellation); other = pool.submit(claim)
        result, claimed = future.result(), other.result()
    saved = rows(r)['jobs'][0]
    if result == 'cancelled':
        assert claimed is None and saved['attempt_no'] == 0 and saved['state'] == 'failed'
    else:
        assert result == 'FRAMEWORK_TASK_NOT_QUEUED'
        assert claimed is not None and saved['state'] == 'running' and saved['attempt_no'] == 1


def test_hidden_history_survives_reload_and_can_be_restored_without_changing_records(runtime):
    r = runtime; start(r); assert cancel(r).status_code == 200
    original = rows(r)
    assert visibility(r, True).json()['hidden'] is True
    assert visibility(r, True).json()['hidden'] is True
    assert MyTasks(r.store).list(r.subject)['items'] == []
    hidden = MyTasks(r.store).list(r.subject, visibility='hidden')['items']
    assert len(hidden) == 1 and hidden[0]['run_id'] == r.context.run_id
    assert r.client.get(BASE + '/runs/' + r.context.run_id).status_code == 200
    assert rows(r) == original
    assert visibility(r, False).json()['hidden'] is False
    assert MyTasks(r.store).list(r.subject)['items'][0]['hidden'] is False
    assert rows(r) == original
    with r.db.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(events).where(events.c.run_id == r.context.run_id,
                         events.c.kind == 'history.visibility_changed')).scalar_one() == 2


def test_completed_history_keeps_results_and_approvals(runtime):
    r = runtime; start(r); work(r)
    assert visibility(r, True).status_code == 409  # Criteria still need review.
    assert approve(r, 'approve_criteria').status_code == 200
    work(r); assert visibility(r, True).status_code == 409  # Human approval still pending.
    assert approve(r, 'approve_final').status_code == 200
    original = rows(r)
    assert original['steps'] and original['approvals']
    assert visibility(r, True).status_code == 200
    assert rows(r) == original


def test_other_project_owner_cannot_see_cancel_or_hide_my_task(runtime):
    r = runtime; start(r); other = email_user(r.auth_database)
    with r.owner.begin() as c:
        c.execute(insert(table('project_members')).values(project_id=UUID(r.context.project_id),
                  user_id=UUID(other['id']), role='owner'))
    app = create_postgres_app(r.settings, r.configuration, engine=r.db.engine)
    with TestClient(app, base_url='http://localhost:8000', client=('127.0.0.1', 33)) as client:
        client.headers['Origin'] = 'http://localhost:8000'
        assert client.get(BASE + '/my-tasks').status_code == 401
        assert login(client, email=other['email']).status_code == 200
        assert client.get(BASE + '/my-tasks?visibility=all').json()['items'] == []
        for suffix, body in [('cancel', {}), ('history-visibility', {'hidden': True})]:
            assert client.post(BASE + '/runs/' + r.context.run_id + '/' + suffix,
                               json=body, headers=csrf(client)).status_code == 404
        assert client.get(BASE + '/my-tasks?before=' + r.context.run_id).status_code == 422
    assert rows(r)['jobs'][0]['state'] == 'queued'


def test_visibility_filter_runs_before_pagination_and_validates_input(runtime):
    r = runtime; identifiers = []
    for index in range(4):
        context = r.context.model_copy(update={'run_id': str(uuid4())})
        r.store.start(r.subject, context, r.document, r.asset)
        MyTasks(r.store).cancel(r.subject, context.run_id)
        identifiers.append(context.run_id)
    MyTasks(r.store).visibility(r.subject, identifiers[-1], True)
    found = []; before = ''
    while True:
        reply = r.client.get(BASE + '/my-tasks', params={'limit': 1, **({'before': before} if before else {})})
        assert reply.status_code == 200, reply.text
        page = reply.json(); found.extend(item['run_id'] for item in page['items'])
        if not page['has_more']: break
        before = page['next_cursor']
    assert found == list(reversed(identifiers[:-1]))
    assert len(r.client.get(BASE + '/my-tasks?visibility=all').json()['items']) == 4
    assert len(r.client.get(BASE + '/my-tasks?visibility=hidden').json()['items']) == 1
    assert r.client.get(BASE + '/my-tasks?visibility=invalid').status_code == 422
    assert r.client.get(BASE + '/my-tasks?limit=101').status_code == 422
    assert visibility(r, 'true').status_code == 422


def test_revoked_membership_disallows_controls_and_listing(runtime):
    r = runtime; start(r); successor = email_user(r.auth_database)
    with r.owner.begin() as c:
        c.execute(insert(table('project_members')).values(project_id=UUID(r.context.project_id),
                  user_id=UUID(successor['id']), role='owner'))
        c.execute(update(table('project_members')).where(table('project_members').c.project_id == UUID(r.context.project_id),
                  table('project_members').c.user_id == r.subject.user_id).values(is_active=False))
    assert r.client.get(BASE + '/my-tasks').json()['items'] == []
    assert cancel(r).status_code == 404
    assert visibility(r, True).status_code == 404
    assert rows(r)['jobs'][0]['state'] == 'queued'
