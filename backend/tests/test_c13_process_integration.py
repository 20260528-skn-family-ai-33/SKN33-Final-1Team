"""Real HTTP web process and independent worker processes, using synthetic OCR only."""
from contextlib import contextmanager
from pathlib import Path
import os,sys,time
from uuid import UUID,uuid4
import httpx,pytest
from sqlalchemy import select
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user,login
from backend.tests.auth_fixtures import auth_environment
from backend.tests.test_c10_reviews_integration import setup,submit
from backend.tests.c12_helpers import process,server,clean_environment
from backend.app.storage.local import LocalStorage
from backend.app.persistence.transaction import unit_of_work
from backend.app.services.business import table,one

pytestmark=pytest.mark.db_integration


def wait(predicate,timeout=10):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=predicate()
        if value:return value
        time.sleep(.04)
    raise AssertionError('Owned C13 process transition timeout')


@contextmanager
def worker(db,root,gate,log,*,before=False,once=False):
    values=auth_environment(db,STORAGE_ROOT=str(root),REVIEW_EXECUTOR='postgres_worker',REVIEW_PIPELINE='paddle-only',LOCAL_ADMIN_LOGIN='0')
    values={key:value for key,value in values.items() if not key.startswith('AUTH_') and key not in ('CSRF_SECRET','CURSOR_SECRET','DEV_PROJECT_ID')}
    path=root.parent/('worker-'+uuid4().hex+'.env')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write('\n'.join(k+'='+v for k,v in values.items())+'\n')
    command=[sys.executable,'-m','backend.tests.c13_worker_process','--env-file',str(path),'--gate',str(gate)]
    if before:command+=['--before-dispatch']
    if once:command+=['--once']
    try:
        with process(command,log,env=clean_environment()) as child:yield child
    finally:path.unlink()


def run_state(db,run_id):
    with unit_of_work(db.engine) as c:
        run=one(c,'review_runs','run_id',UUID(str(run_id)))
        attempts=[dict(row) for row in c.execute(select(table('review_attempts')).where(table('review_attempts').c.run_id==run['run_id']).order_by(table('review_attempts').c.attempt_no)).mappings()]
    return run,attempts


def bootstrap(db,root,log,user):
    # Return only a committed run ID/body; the web process is fully stopped here.
    with server(db,root,log,executor='postgres_worker') as (url,_):
        with httpx.Client(base_url=url,headers={'Origin':url},trust_env=False,timeout=5) as client:
            assert login(client,email=user['email']).status_code==200
            body,_=setup(client);response=submit(client,body);assert response.status_code==202,response.text
            assert client.get('/api/v1/reviews/'+response.json()['id']).json()['status']=='queued'
            return response.json()['id'],body,client.cookies


def test_web_stopped_worker_completes_and_web_restart_reads_same_run(auth_database,tmp_path):
    db=auth_database[0];root=tmp_path.resolve()/'files';LocalStorage.initialize(root);gate=tmp_path/'gate';gate.mkdir();(gate/'allow').touch()
    user=email_user(auth_database)
    with (tmp_path/'live.log').open('w+') as log:
        run_id,body,cookies=bootstrap(db,root,log,user)
        # Two independent workers share storage and compete for one queued run.
        with worker(db,root,gate,log,once=True) as first,worker(db,root,gate,log,once=True) as second:
            assert first.wait(timeout=15)==0 and second.wait(timeout=15)==0
        run,attempts=run_state(db,run_id);assert run['execution_status']=='completed' and len(attempts)==1
        assert len((gate/'calls').read_text().splitlines())==1
        with server(db,root,log,executor='postgres_worker') as (url,_):
            with httpx.Client(base_url=url,cookies=cookies,headers={'Origin':url},trust_env=False,timeout=5) as client:
                response=client.get('/api/v1/reviews/'+run_id);assert response.status_code==200 and response.json()['results_complete']
    log_source=(tmp_path/'live.log').read_text();assert db.settings.database.password.reveal() not in log_source


@pytest.mark.parametrize('before',(True,False))
def test_sigkill_recovers_only_predispatch_without_automatic_ocr_replay(auth_database,tmp_path,before):
    db=auth_database[0];root=tmp_path.resolve()/'files';LocalStorage.initialize(root);gate=tmp_path/'gate';gate.mkdir()
    user=email_user(auth_database)
    with (tmp_path/'crash.log').open('w+') as log:
        run_id,_,_=bootstrap(db,root,log,user)
        with worker(db,root,gate,log,before=before) as child:
            wait(lambda:(gate/('claimed' if before else 'entered')).exists())
            child.kill();child.wait(timeout=3)  # Only the owned synthetic worker is killed.
        time.sleep(1.15)
        (gate/'allow').touch();(gate/'claim-allow').touch()
        with worker(db,root,gate,log) as replacement:
            expected='completed' if before else 'failed'
            wait(lambda:run_state(db,run_id)[0]['execution_status']==expected,timeout=12)
            replacement.terminate();assert replacement.wait(timeout=5)==0
        run,attempts=run_state(db,run_id)
        if before:
            assert len(attempts)==2 and attempts[0]['retryable'] is True and attempts[1]['execution_status']=='succeeded'
            assert len((gate/'calls').read_text().splitlines())==1
        else:
            assert len(attempts)==1 and attempts[0]['execution_status']=='unknown' and run['error_jsonb']['outcome_unknown']
            assert len((gate/'calls').read_text().splitlines())==1


def test_sigterm_drains_active_work_and_does_not_claim_next_run(auth_database,tmp_path):
    db=auth_database[0];root=tmp_path.resolve()/'files';LocalStorage.initialize(root);gate=tmp_path/'gate';gate.mkdir()
    user=email_user(auth_database)
    with (tmp_path/'drain.log').open('w+') as log:
        first,_,_=bootstrap(db,root,log,user);second,_,_=bootstrap(db,root,log,user)
        with worker(db,root,gate,log) as child:
            wait(lambda:(gate/'entered').exists());child.terminate()
            time.sleep(1.3);assert child.poll() is None
            (gate/'allow').touch();assert child.wait(timeout=8)==0
        first_state,attempts=run_state(db,first);second_state,_=run_state(db,second)
        assert first_state['execution_status']=='completed' and second_state['execution_status']=='queued'
        assert len((gate/'calls').read_text().splitlines())==1
        # Finish the other explicitly queued synthetic job so the module has no pending work.
        with worker(db,root,gate,log,once=True) as child:assert child.wait(timeout=10)==0
        assert run_state(db,second)[0]['execution_status']=='completed'
