"""Real, restricted PostgreSQL 17 in a NEW test DB; never an operational DB.

Run explicitly with -m db_integration and TEST_DB_ENV_FILE. Existing repository
fixtures create/drop only a random creative_checker_test_* database.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import json,os,subprocess,sys
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,insert,update,func

from tests.app.test_db_integration import database
from tests.app.test_v1_auth_integration import auth_database,email_user,login,csrf
from backend.migrations.configuration import load_migration_settings
from backend.app.services.business import table
from backend.app.persistence.database import PersistenceError
from backend.app.persistence.schema_inventory import check_schema
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from backend.agent_framework.adapters.synthetic import make_agents,sample_inputs
from backend.agent_framework.composition import load_config
from backend.agent_framework.contracts import Approval,FrameworkError
from backend.agent_framework.postgres import PostgresStore,runs,jobs,steps,approvals,events,payload
from backend.agent_framework.postgres_schema import migrate,check_extension
from backend.agent_framework.postgres_api import create_postgres_app
from backend.agent_framework.durable_worker import PostgresQueue,execute_claim

pytestmark=pytest.mark.db_integration
PREFIX='/api/framework/v1/runs'


@pytest.fixture(scope='module')
def framework_database(auth_database):
    db,owner,_manager,settings,_setup=auth_database
    env=db.environment
    migration=load_migration_settings({'DB_HOST':env['TEST_DB_HOST'],'DB_PORT':env['TEST_DB_PORT'],
        'POSTGRES_DB':db.name,'DB_OWNER_USER':env['TEST_DB_OWNER_USER'],
        'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'],'DB_APP_USER':env['TEST_DB_APP_USER']})
    before=check_schema(db.engine,db.settings)
    assert migrate(migration)['revision']=='AF001'
    assert migrate(migration)['revision']=='AF001'
    assert check_schema(db.engine,db.settings)==before
    yield auth_database,migration


@pytest.fixture
def runtime(framework_database):
    auth_database,_migration=framework_database
    db,owner,_manager,settings,_setup=auth_database
    configuration=load_config('examples/agent_framework/team_config.synthetic.json')
    user=email_user(auth_database)
    app=create_postgres_app(settings,configuration,engine=db.engine)
    with TestClient(app,base_url='http://localhost:8000',client=('127.0.0.1',12)) as client:
        client.headers['Origin']='http://localhost:8000'
        assert login(client,email=user['email']).status_code==200
        subject=app.state.auth.current(client.cookies[settings.session_cookie])
        reply=client.post('/api/v1/projects',json={'name':'Framework test project'},headers=csrf(client))
        assert reply.status_code==201,reply.text
        project=reply.json()
        context,document,asset=sample_inputs(run_id='pg-'+uuid4().hex)
        context=context.model_copy(update={'project_id':project['id'],'actor_id':user['id']})
        store=app.state.framework
        current=SimpleNamespace(client=client,db=db,owner=owner,settings=settings,configuration=configuration,
            context=context,document=document,asset=asset,store=store,queue=PostgresQueue(store),subject=subject,
            auth_database=auth_database)
        yield current
        # Clean up pending synthetic jobs only in this random test DB/project.
        with owner.begin() as c:
            pending=c.execute(select(jobs).where(jobs.c.project_id==UUID(project['id']),jobs.c.state.in_(('queued','running')))).mappings().all()
            for job in pending:
                row=c.execute(select(runs).where(runs.c.run_id==job['run_id'])).mappings().one()
                current.queue._close(c,dict(row),dict(job),FrameworkError(code='TEST_CLEANUP',message='Synthetic fixture cleanup.'))


def start(r):
    response=r.client.post(PREFIX,json={'context':payload(r.context),'document':payload(r.document),'asset':payload(r.asset)},headers=csrf(r.client))
    assert response.status_code==202,response.text
    return response.json()


def approve(r,decision,*,context=None,**options):
    ctx=context or r.context
    return r.client.post(PREFIX+'/'+ctx.run_id+'/approvals',json=payload(Approval(context=ctx,
        actor_id=ctx.actor_id,decision=decision,**options)),headers=csrf(r.client))


def work(r,agents=None):
    # A newly constructed store/queue has no in-process run state to reuse.
    store=PostgresStore(r.db.engine,r.configuration)
    queue=PostgresQueue(store)
    claim=queue.claim_next();assert claim is not None
    assert execute_claim(queue,claim,agents or make_agents())=='completed'
    return claim


def expire(r,claim):
    with r.owner.begin() as c:
        c.execute(update(jobs).where(jobs.c.job_id==claim.job_id).values(lease_expires_at=func.clock_timestamp()-__import__('datetime').timedelta(seconds=1)))


def test_project_metadata_routes_reuse_session_csrf_and_membership_without_media(runtime):
    r=runtime;path='/api/v1/projects/'+r.context.project_id
    assert not hasattr(r.client.app.state,'storage')
    assert not hasattr(r.client.app.state,'files')
    assert not hasattr(r.client.app.state,'review_executor')
    listing=r.client.get('/api/v1/projects?limit=100')
    assert listing.status_code==200,listing.text
    assert r.context.project_id in {p['id'] for p in listing.json()['items']}
    assert r.client.get(path).json()['my_role']=='owner'
    assert r.client.post('/api/v1/projects',json={'name':'Missing CSRF'}).status_code==403
    assert r.client.patch(path,json={'name':'Missing CSRF'}).status_code==403
    changed=r.client.patch(path,json={'name':'Renamed framework project'},headers=csrf(r.client))
    assert changed.status_code==200 and changed.json()['name']=='Renamed framework project'
    assert r.client.get(path+'/images').status_code==404

    app=create_postgres_app(r.settings,r.configuration,engine=r.db.engine)
    with TestClient(app,base_url='http://localhost:8000',client=('127.0.0.1',46)) as other:
        other.headers['Origin']='http://localhost:8000'
        assert other.get('/api/v1/projects').status_code==401
        outsider=email_user(r.auth_database)
        assert login(other,email=outsider['email']).status_code==200
        assert other.get('/api/v1/projects').json()['items']==[]
        assert other.get(path).status_code==404
        assert other.patch(path,json={'name':'Denied'},headers=csrf(other)).status_code==404
    assert r.client.get(path).json()['name']=='Renamed framework project'


def test_api_worker_approvals_and_rereview_survive_new_instances(runtime):
    r=runtime
    assert start(r)['execution_status']=='queued'
    assert start(r)['execution_status']=='queued'  # same fixed run is idempotent
    with unit_of_work(r.db.engine) as c:assert not c.execute(select(steps).where(steps.c.run_id==r.context.run_id)).all()
    claim=work(r)
    assert r.store.get(r.subject,r.context.run_id).execution_status=='waiting_criteria'
    with unit_of_work(r.db.engine) as c:
        job=c.execute(select(jobs).where(jobs.c.job_id==claim.job_id)).mappings().one()
        assert job['state']=='completed' and job['worker_token'] is job['lease_expires_at'] is None
    assert approve(r,'approve_criteria').json()['execution_status']=='queued'
    work(r)
    assert r.store.get_steps(r.subject,r.context.run_id).storage=='postgres'
    assert approve(r,'approve_criteria').json()['execution_status']=='waiting_review'
    parent=approve(r,'approve_changes',reason='Fix the poster').json()
    revised,_,asset=sample_inputs('pg-child-'+uuid4().hex,'poster-v2',r.context.run_id)
    revised=revised.model_copy(update={'project_id':r.context.project_id,'actor_id':r.context.actor_id})
    request={'context':payload(revised),'asset':payload(asset)}
    first=r.client.post(PREFIX+'/'+r.context.run_id+'/revisions',json=request,headers=csrf(r.client))
    assert first.status_code==201,first.text
    again=r.client.post(PREFIX+'/'+r.context.run_id+'/revisions',json=request,headers=csrf(r.client))
    assert again.json()==first.json()
    assert first.json()['step_records'][0]['status']=='reused'
    assert approve(r,'approve_criteria',context=revised).json()['execution_status']=='queued'
    work(r)
    assert approve(r,'approve_final',context=revised).json()['human_decision']=='final_approved'
    # A completely new authenticated application instance reads stored results.
    app=create_postgres_app(r.settings,r.configuration,engine=r.db.engine)
    with TestClient(app,base_url='http://localhost:8000') as second:
        second.cookies.update(r.client.cookies)
        assert second.get(PREFIX+'/'+r.context.run_id).json()==parent
        child=second.get(PREFIX+'/'+revised.run_id).json()
        assert child['execution_status']=='completed' and child['human_decision']=='final_approved'
        history=second.get(PREFIX+'/'+revised.run_id+'/approvals').json()
        assert len(history)==2 and all(row['approved_at'] for row in history)
    with unit_of_work(r.db.engine) as c:
        assert len(c.execute(select(steps).where(steps.c.run_id.in_((r.context.run_id,revised.run_id)))).all())==8
        assert len(c.execute(select(approvals).where(approvals.c.run_id.in_((r.context.run_id,revised.run_id)))).all())==4


def test_two_workers_claim_once_and_late_tokens_cannot_write(runtime):
    r=runtime;start(r);barrier=Barrier(2)
    def claim(_):barrier.wait();return PostgresQueue(r.store).claim_next()
    with ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(claim,range(2)))
    live=[c for c in claims if c is not None]
    assert len(live)==1
    with pytest.raises(AuthError):r.queue.heartbeat(replace(live[0],token=uuid4()))
    assert execute_claim(r.queue,live[0],make_agents())=='completed'
    with pytest.raises(AuthError):r.queue.heartbeat(live[0])


def test_expired_before_dispatch_is_safe_but_after_dispatch_is_unknown(runtime):
    r=runtime;start(r)
    first=r.queue.claim_next();expire(r,first)
    assert r.queue.recover_expired()[r.context.run_id]=='queued'
    second=r.queue.claim_next()
    request=r.queue.begin_step(second,'A02')
    expire(r,second)
    assert r.queue.recover_expired()[r.context.run_id]=='outcome_unknown'
    assert r.queue.claim_next() is None
    snapshot=r.store.get(r.subject,r.context.run_id)
    assert snapshot.execution_status=='outcome_unknown' and snapshot.step_records[-1].duration_ms is None
    result=make_agents().extractor.extract(request)
    with pytest.raises(AuthError):r.queue.complete_step(second,'A02',request,result,1)


def test_completed_inspection_is_reused_after_worker_loss(runtime):
    r=runtime;start(r);work(r);assert approve(r,'approve_criteria').status_code==200
    claim=r.queue.claim_next();request=r.queue.begin_step(claim,'A04_A05')
    result=make_agents().inspector.inspect(request)
    r.queue.complete_step(claim,'A04_A05',request,result,1)
    expire(r,claim);assert r.queue.recover_expired()[r.context.run_id]=='queued'
    agents=make_agents()
    def forbidden(_):raise AssertionError('Committed inspection must never be repeated')
    agents=replace(agents,inspector=SimpleNamespace(inspect=forbidden))
    work(r,agents)
    snapshot=r.store.get(r.subject,r.context.run_id)
    assert snapshot.execution_status=='waiting_review' and len(snapshot.step_records)==4


def test_worker_ignores_mismatched_configuration_and_code_revision(runtime):
    r=runtime;start(r)
    other=PostgresStore(r.db.engine,r.configuration)
    other.config_hash='0'*64
    assert PostgresQueue(other).claim_next() is None
    other.config_hash=r.store.config_hash;other.runtime_hash='0'*64
    assert PostgresQueue(other).claim_next() is None
    work(r)


def test_current_membership_is_rechecked_before_execution(runtime):
    r=runtime;start(r)
    replacement=email_user(r.auth_database)
    with r.owner.begin() as c:
        c.execute(insert(table('project_members')).values(project_id=UUID(r.context.project_id),
            user_id=UUID(replacement['id']),role='owner',is_active=True))
        c.execute(update(table('project_members')).where(table('project_members').c.project_id==UUID(r.context.project_id),
            table('project_members').c.user_id==UUID(r.context.actor_id)).values(role='viewer'))
    assert r.queue.claim_next() is None
    assert r.store.get(r.subject,r.context.run_id).execution_status=='failed'
    assert approve(r,'approve_criteria').status_code==403


def test_existing_sessions_csrf_and_actor_scope_are_enforced(runtime):
    r=runtime;start(r)
    with TestClient(create_postgres_app(r.settings,r.configuration,engine=r.db.engine),base_url='http://localhost:8000') as anonymous:
        assert anonymous.get(PREFIX+'/'+r.context.run_id).status_code==401
    assert r.client.post(PREFIX,json={'context':payload(r.context),'document':payload(r.document),'asset':payload(r.asset)},
        headers={'X-CSRFToken':'invalid'}).status_code==403
    foreign=r.context.model_copy(update={'actor_id':str(uuid4())})
    assert r.client.post(PREFIX,json={'context':payload(foreign),'document':payload(r.document),'asset':payload(r.asset)},
        headers=csrf(r.client)).status_code==403


def test_unknown_commit_is_reconciled_without_replaying_the_role(runtime,monkeypatch):
    r=runtime;start(r);claim=r.queue.claim_next()
    original=r.queue.complete_step
    def acknowledged_lost(*args,**kwargs):
        original(*args,**kwargs)
        raise PersistenceError('DB_COMMIT_OUTCOME_UNKNOWN','Synthetic lost acknowledgement',outcome='commit_unknown')
    monkeypatch.setattr(r.queue,'complete_step',acknowledged_lost)
    assert execute_claim(r.queue,claim,make_agents())=='database_unavailable'
    assert r.store.get(r.subject,r.context.run_id).execution_status=='waiting_criteria'
    assert r.queue.recover_expired()=={} and r.queue.claim_next() is None


def test_sealed_outputs_and_approval_rows_are_not_rewritable(runtime):
    r=runtime;start(r);work(r);assert approve(r,'approve_criteria').status_code==200
    with pytest.raises(PersistenceError):
        with unit_of_work(r.db.engine) as c:
            c.execute(update(steps).where(steps.c.run_id==r.context.run_id).values(output_sha256='0'*64))
    with pytest.raises(PersistenceError):
        with unit_of_work(r.db.engine) as c:
            c.execute(update(approvals).where(approvals.c.run_id==r.context.run_id).values(payload_sha256='0'*64))
    assert check_schema(r.db.engine,r.db.settings)['revision']=='M008'


def test_separate_worker_process_loads_only_framework_queue(runtime,tmp_path):
    r=runtime;start(r)
    settings=r.db.settings.database
    env=tmp_path/'worker.env'
    env.write_text('\n'.join(['PERSISTENCE_MODE=postgres','APP_ENV=test','DB_HOST='+settings.host,
        'DB_PORT='+str(settings.port),'POSTGRES_DB='+settings.database,'DB_APP_USER='+settings.username,
        'DB_APP_PASSWORD='+settings.password.reveal()])+'\n', encoding="utf-8");env.chmod(0o600)
    result=subprocess.run([sys.executable,'-m','backend.agent_framework','worker','--env-file',str(env),
        '--config','examples/agent_framework/team_config.synthetic.json','--once'],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'framework_worker_ready' in result.stdout and 'completed' in result.stdout
    assert r.store.get(r.subject,r.context.run_id).execution_status=='waiting_criteria'


def test_changed_approval_payload_and_revised_context_are_rejected(runtime):
    r=runtime;start(r);work(r);assert approve(r,'approve_criteria').status_code==200
    work(r);assert approve(r,'approve_changes',reason='First decision').status_code==200
    assert approve(r,'approve_changes',reason='Changed decision').status_code==409
    revised,_,asset=sample_inputs('child-'+uuid4().hex,'poster-v2',r.context.run_id)
    revised=revised.model_copy(update={'project_id':r.context.project_id,'actor_id':r.context.actor_id})
    assert r.store.rereview(r.subject,r.context.run_id,revised,asset).context==revised
    with pytest.raises(AuthError):
        r.store.rereview(r.subject,r.context.run_id,revised.model_copy(update={'run_id':'another-child'}),asset)


def test_heartbeat_failure_permanently_blocks_late_results(runtime,monkeypatch):
    from backend.agent_framework import durable_worker
    r=runtime;start(r);claim=r.queue.claim_next()
    class LostLease:
        def __init__(self,*_):self.calls=0
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def check(self):
            self.calls+=1
            if self.calls>1:raise AuthError('FRAMEWORK_AUTHORITY_LOST',409)
    monkeypatch.setattr(durable_worker,'Lease',LostLease)
    assert execute_claim(r.queue,claim,make_agents())=='authority_lost'
    snapshot=r.store.get(r.subject,r.context.run_id)
    assert snapshot.criteria is None and snapshot.step_records[-1].status=='running'
    expire(r,claim);assert r.queue.recover_expired()[r.context.run_id]=='outcome_unknown'


def test_revocation_between_model_call_and_commit_rejects_output(runtime):
    r=runtime;start(r);claim=r.queue.claim_next();request=r.queue.begin_step(claim,'A02')
    result=make_agents().extractor.extract(request)
    with r.owner.begin() as c:
        c.execute(update(table('projects')).where(table('projects').c.project_id==UUID(r.context.project_id)).values(
            business_state='archived',archived_at=func.clock_timestamp()))
    with pytest.raises(AuthError):r.queue.complete_step(claim,'A02',request,result,1)
    assert r.store.get(r.subject,r.context.run_id).criteria is None


def test_extension_schema_and_privilege_drift_fail_readiness(runtime):
    from backend.agent_framework.postgres_schema import check_app_permissions
    r=runtime
    role=r.owner.dialect.identifier_preparer.quote(r.db.settings.database.username)
    with r.owner.begin() as c:c.exec_driver_sql('GRANT UPDATE ON agent_framework.approvals TO '+role)
    try:
        with pytest.raises(PersistenceError):
            with unit_of_work(r.db.engine) as c:check_app_permissions(c)
    finally:
        with r.owner.begin() as c:c.exec_driver_sql('REVOKE UPDATE ON agent_framework.approvals FROM '+role)
    with r.owner.begin() as c:c.exec_driver_sql('ALTER TABLE agent_framework.jobs ADD COLUMN synthetic_drift integer')
    try:
        with pytest.raises(PersistenceError):
            with unit_of_work(r.db.engine) as c:check_extension(c)
    finally:
        with r.owner.begin() as c:c.exec_driver_sql('ALTER TABLE agent_framework.jobs DROP COLUMN synthetic_drift')


def test_commit_boundary_revalidates_scope_without_another_provider_call(runtime):
    from backend.agent_framework.invocation import InvocationFailure
    r=runtime;start(r);claim=r.queue.claim_next();request=r.queue.begin_step(claim,'A02')
    result=make_agents().extractor.extract(request)
    tampered=result.model_copy(update={'context':result.context.model_copy(update={'project_id':str(uuid4())})})
    with pytest.raises(InvocationFailure):r.queue.complete_step(claim,'A02',request,tampered,1)
    assert r.store.get(r.subject,r.context.run_id).criteria is None


def test_provider_executes_without_holding_database_connection_or_run_lock(runtime):
    r=runtime;start(r)
    agents=make_agents()
    base=agents.extractor
    def extract(request):
        assert r.db.engine.pool.checkedout()==0
        with unit_of_work(r.db.engine) as c:
            assert c.execute(select(runs.c.run_id).where(runs.c.run_id==r.context.run_id)
                .with_for_update(nowait=True)).scalar_one()==r.context.run_id
        return base.extract(request)
    work(r,replace(agents,extractor=SimpleNamespace(extract=extract)))
