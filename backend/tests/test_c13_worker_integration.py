"""Restricted real PostgreSQL, shared filesystem and durable worker authority."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import threading,time
from contextlib import contextmanager
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,update,func
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user,login
from backend.tests.test_c10_reviews_integration import setup,submit
from backend.tests.c10_fixtures import SyntheticPipeline
from backend.app.main import create_app
from backend.app.worker import AttemptLease
from backend.app.execution.queue import WorkerQueue,WORKER_RETRY
from backend.app.services.reviews import Reviews
from backend.app.services.review_results import ReviewExecutor
from backend.app.services.business import table,one
from backend.app.persistence.transaction import unit_of_work
from backend.app.persistence.database import PersistenceError
from backend.app.storage.local import LocalStorage,StorageError
from backend.app.security.errors import AuthError

pytestmark=pytest.mark.db_integration


@pytest.fixture
def client(auth_database,tmp_path):
    root=tmp_path.resolve()/'files';LocalStorage.initialize(root)
    settings=replace(auth_database[3],storage_root=root,review_executor='postgres_worker')
    adapter=SyntheticPipeline()
    with TestClient(create_app(settings=settings,engine=auth_database[0].engine,review_adapter=adapter),base_url='http://localhost:8000',client=('127.0.0.1',12)) as current:
        current.headers['Origin']='http://localhost:8000'
        user=email_user(auth_database);assert login(current,email=user['email']).status_code==200
        current.pipeline=adapter;current.queue=WorkerQueue(auth_database[0].engine,current.app.state.storage,adapter)
        yield current
        # Only this module's isolated DB. End unexecuted synthetic claims; retain every graph.
        queue=current.queue;queue.recover_expired()
        with unit_of_work(queue.engine) as c:
            rows=c.execute(select(table('review_runs')).where(table('review_runs').c.execution_status.in_(('queued','running')))).mappings().all()
        for row in rows:
            if row['active_attempt_id']:
                with unit_of_work(queue.engine) as c:attempt=one(c,'review_attempts','attempt_id',row['active_attempt_id'])
                queue.fail(row['run_id'],attempt['attempt_id'],attempt['worker_token'],code='SYNTHETIC_TEST_CLEANUP')
            else:Reviews.fail(queue,row['run_id'],code='SYNTHETIC_TEST_CLEANUP')


def enqueue(client):
    body,_=setup(client);response=submit(client,body);assert response.status_code==202,response.text
    return UUID(response.json()['id']),body


def state(client,run_id):
    with unit_of_work(client.queue.engine) as c:
        run=one(c,'review_runs','run_id',run_id)
        attempts=c.execute(select(table('review_attempts')).where(table('review_attempts').c.run_id==run_id).order_by(table('review_attempts').c.attempt_no)).mappings().all()
    return run,[dict(row) for row in attempts]


def execute(client,claimed,*,interval=.02):
    with AttemptLease(client.queue,claimed,interval=interval) as authority:
        executor=ReviewExecutor(client.queue,authority);executor.execute_claimed(claimed)
    return executor


def ready_again(client,run_id):
    with unit_of_work(client.queue.engine) as c:
        c.execute(update(table('review_runs')).where(table('review_runs').c.run_id==run_id).values(available_at=func.clock_timestamp()))


def test_web_only_admits_and_replays_worker_jobs(client):
    body,_=setup(client);key=uuid4();first=submit(client,body,key);again=submit(client,body,key)
    assert first.status_code==202 and again.status_code==200 and again.json()['id']==first.json()['id']
    assert client.pipeline.calls==0 and client.app.state.review_executor is None
    run,attempts=state(client,UUID(first.json()['id']))
    assert run['executor_kind']=='postgres_worker' and run['retry_policy_jsonb']==WORKER_RETRY and attempts==[]
    assert client.get('/api/v1/health').json()['checks']['worker']['state']=='unknown'


def test_two_workers_claim_once_then_commit_complete_graph(client):
    run_id,body=enqueue(client)
    with ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(lambda _:client.queue.claim_next(),range(2)))
    assert sum(value is not None for value in claims)==1
    execute(client,next(value for value in claims if value))
    run,attempts=state(client,run_id)
    assert run['execution_status']=='completed' and run['sealed_at'] and len(attempts)==1
    assert attempts[0]['execution_status']=='succeeded' and attempts[0]['dispatch_state']=='dispatching' and attempts[0]['remote_job_id'] is None
    assert client.pipeline.calls==1
    data=client.get('/api/v1/reviews/'+str(run_id)).json();assert data['results_complete'] and len(data['results'])==1
    exported=client.get('/api/v1/reviews/'+str(run_id)+'/export');assert exported.status_code==200
    assert all(secret not in exported.text for secret in ('worker_token','lease_expires_at','storage_key'))


def test_skip_locked_projects_and_available_time(client):
    first,body=enqueue(client);second,_=enqueue(client)
    with unit_of_work(client.queue.engine) as held:
        held.execute(select(table('projects')).where(table('projects').c.project_id==UUID(body['project_id'])).with_for_update())
        with ThreadPoolExecutor(max_workers=1) as pool:claim=pool.submit(client.queue.claim_next).result(timeout=2)
        assert claim[0]['run_id']==second
    with unit_of_work(client.queue.engine) as c:
        c.exec_driver_sql("UPDATE public.review_runs SET available_at=clock_timestamp()+interval '1 hour' WHERE run_id=%s",(first,))
    assert client.queue.claim_next() is None


def test_heartbeat_runs_while_inference_has_no_db_transaction(client):
    run_id,_=enqueue(client);claim=client.queue.claim_next();entered=threading.Event();release=threading.Event()
    original=client.pipeline.recognize
    def blocked(*args):entered.set();assert release.wait(4);return original(*args)
    client.pipeline.recognize=blocked
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(execute,client,claim)
        try:
            assert entered.wait(2)
            before=state(client,run_id)[1][0]['heartbeat_at'];time.sleep(.09)
            after=state(client,run_id)[1][0]['heartbeat_at'];assert after>before
            with unit_of_work(client.queue.engine) as c:
                # A short heartbeat may legitimately own this lock. Bound that wait
                # while proving it never waits for the blocked OCR call to finish.
                c.exec_driver_sql("SET LOCAL lock_timeout='500ms'")
                c.execute(select(table('review_runs')).where(table('review_runs').c.run_id==run_id).with_for_update())
        finally:release.set()
        future.result(timeout=4)
    assert state(client,run_id)[0]['execution_status']=='completed'


def test_expired_predispatch_requeues_with_fresh_token_and_three_attempt_cap(client):
    client.queue.lease_seconds=.25;run_id,_=enqueue(client);started=None
    for number in range(1,4):
        claim=client.queue.claim_next();assert claim and claim[0]['run_id']==run_id
        run,attempts=state(client,run_id);started=started or run['started_at'];assert started==run['started_at']
        assert attempts[-1]['attempt_no']==number
        time.sleep(.3)
        with pytest.raises(AuthError):client.queue.heartbeat(run_id,claim[1],claim[2])
        outcome=client.queue.recover_expired();assert outcome[str(run_id)]==('requeued' if number<3 else 'failed')
        run,attempts=state(client,run_id)
        assert attempts[-1]['execution_status']=='failed' and not attempts[-1]['error_jsonb']['outcome_unknown']
        with pytest.raises(AuthError):client.queue.advance(run_id,claim[1],claim[2],'ocr',30)
        if number<3:
            assert run['available_at']>attempts[-1]['finished_at'] and run['finished_at'] is None
            assert client.queue.claim_next() is None
            ready_again(client,run_id)
    assert run['execution_status']=='failed' and client.pipeline.calls==0
    assert len({value['worker_token'] for value in attempts})==3


@pytest.mark.parametrize('dispatch',('dispatching','submitted','unknown'))
def test_expired_after_dispatch_never_resubmits(client,dispatch):
    client.queue.lease_seconds=.25;run_id,_=enqueue(client);claim=client.queue.claim_next()
    client.queue.dispatch(run_id,claim[1],claim[2])
    if dispatch in ('submitted','unknown'):
        with unit_of_work(client.queue.engine) as c:
            c.execute(update(table('review_attempts')).where(table('review_attempts').c.attempt_id==claim[1]).values(dispatch_state=dispatch,remote_job_id='synthetic-existing-job' if dispatch=='submitted' else None))
    time.sleep(.3)
    assert client.queue.recover_expired()[str(run_id)]=='failed'
    run,attempts=state(client,run_id)
    assert run['error_jsonb']['outcome_unknown'] and attempts[0]['execution_status']=='unknown' and attempts[0]['retryable'] is False
    assert client.queue.claim_next() is None and client.pipeline.calls==0


def test_current_token_wrong_token_and_late_result_are_fenced(client):
    run_id,_=enqueue(client);claim=client.queue.claim_next()
    with pytest.raises(AuthError):client.queue.dispatch(run_id,claim[1],uuid4())
    class Authority:
        def check(self):pass
    executor=ReviewExecutor(client.queue,Authority());plan,intent=executor.prepare(*claim)
    assert client.queue.fail(run_id,claim[1],claim[2])
    with pytest.raises(AuthError):executor.persist(plan,claim[2])
    with unit_of_work(client.queue.engine) as c:
        assert c.execute(select(func.count()).select_from(table('item_results')).where(table('item_results').c.run_id==run_id)).scalar_one()==0
    assert state(client,run_id)[0]['selected_attempt_id'] is None


def test_mismatched_config_and_background_queue_are_never_claimed(client):
    run_id,_=enqueue(client)
    other=SyntheticPipeline();other.revision='different'
    assert WorkerQueue(client.queue.engine,client.queue.storage,other).claim_next() is None
    client.app.state.reviews.executor_kind='background_tasks';client.app.state.reviews.workflow='cc-background-v1';client.app.state.reviews.retry={'executor_kind':'background_tasks','max_attempts':1}
    background,_=enqueue(client)
    claim=client.queue.claim_next();assert claim[0]['run_id']==run_id
    assert client.queue.claim_next() is None
    assert state(client,background)[0]['execution_status']=='queued'


def test_live_storage_owners_coexist_and_background_exclusive_is_rejected(client):
    second=LocalStorage(client.queue.storage.path)
    try:
        second.start(shared=True)
        third=LocalStorage(client.queue.storage.path)
        try:
            with pytest.raises(Exception):third.start()
        finally:third.close()
        with client.queue.storage.lock('gate',shared=True):
            assert client.app.state.files.recover(concurrent=True)=={}
    finally:second.close()


def test_lease_is_checked_after_waited_lock_using_actual_db_time(client):
    client.queue.lease_seconds=.3;run_id,_=enqueue(client);claim=client.queue.claim_next()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with unit_of_work(client.queue.engine) as c:
            client.queue.lock_run(c,run_id)
            future=pool.submit(client.queue.heartbeat,run_id,claim[1],claim[2])
            time.sleep(.38)
        with pytest.raises(AuthError):future.result(timeout=2)
    assert client.queue.recover_expired()[str(run_id)]=='requeued'


def test_unconfirmed_claim_never_executes_or_claims_again(client,monkeypatch):
    import backend.app.execution.queue as module
    run_id,_=enqueue(client);real=module.unit_of_work
    @contextmanager
    def lost(*a,**kw):
        with real(*a,**kw) as c:yield c
        raise PersistenceError('DB_COMMIT_OUTCOME_UNKNOWN','synthetic lost acknowledgement',outcome='commit_unknown')
    with monkeypatch.context() as scoped:
        scoped.setattr(module,'unit_of_work',lost)
        with pytest.raises(PersistenceError):client.queue.claim_next()
    run,attempts=state(client,run_id)
    assert run['execution_status']=='running' and len(attempts)==1 and client.pipeline.calls==0
    assert client.queue.claim_next() is None


def test_unconfirmed_dispatch_never_calls_ocr_or_automatically_resubmits(client,monkeypatch):
    run_id,_=enqueue(client);claim=client.queue.claim_next();real=client.queue.dispatch
    def lost(*a):
        real(*a)
        raise PersistenceError('DB_COMMIT_OUTCOME_UNKNOWN','synthetic dispatch response loss',outcome='commit_unknown')
    monkeypatch.setattr(client.queue,'dispatch',lost);execute(client,claim)
    run,attempts=state(client,run_id)
    assert run['execution_status']=='failed' and run['error_jsonb']['outcome_unknown']
    assert attempts[0]['execution_status']=='unknown' and client.pipeline.calls==0 and client.queue.claim_next() is None


def test_result_commit_ack_loss_reconciles_without_second_ocr(client,monkeypatch):
    run_id,_=enqueue(client);claim=client.queue.claim_next()
    with AttemptLease(client.queue,claim,interval=.02) as authority:
        executor=ReviewExecutor(client.queue,authority);real=executor.persist
        def lost(*a):
            real(*a)
            raise PersistenceError('DB_COMMIT_OUTCOME_UNKNOWN','synthetic result response loss',outcome='commit_unknown')
        monkeypatch.setattr(executor,'persist',lost);executor.execute_claimed(claim)
    run,attempts=state(client,run_id)
    assert run['execution_status']=='completed' and len(attempts)==1 and client.pipeline.calls==1
    assert executor.last_success_config==run['requested_config']


def test_expired_plan_cannot_publish_any_database_result(client):
    client.queue.lease_seconds=.35;run_id,_=enqueue(client);claim=client.queue.claim_next()
    class Authority:
        def check(self):pass
    executor=ReviewExecutor(client.queue,Authority());plan,_=executor.prepare(*claim)
    time.sleep(.4)
    with pytest.raises(AuthError):executor.persist(plan,claim[2])
    assert client.queue.recover_expired()[str(run_id)]=='failed'
    run,attempts=state(client,run_id)
    assert run['selected_attempt_id'] is None and run['result_manifest_hash'] is None
    with unit_of_work(client.queue.engine) as c:
        assert c.execute(select(func.count()).select_from(table('item_results')).where(table('item_results').c.run_id==run_id)).scalar_one()==0
