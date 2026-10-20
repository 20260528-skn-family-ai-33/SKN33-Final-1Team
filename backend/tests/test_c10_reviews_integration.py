"""Real restricted PostgreSQL + actual filesystem + ASGI, synthetic OCR only."""
from dataclasses import replace
from uuid import UUID,uuid4
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func,update
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user,login,csrf
from backend.tests.test_c09_business_integration import project,graph,confirm,post
from backend.tests.c10_fixtures import SyntheticPipeline
from backend.app.main import create_app
from backend.app.storage.local import LocalStorage
from backend.app.services.business import table,one
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError

pytestmark=pytest.mark.db_integration


@pytest.fixture
def client(auth_database,tmp_path):
    root=tmp_path.resolve()/'files';LocalStorage.initialize(root)
    settings=replace(auth_database[3],storage_root=root)
    adapter=SyntheticPipeline()
    with TestClient(create_app(settings=settings,engine=auth_database[0].engine,review_adapter=adapter),base_url='http://localhost:8000',client=('127.0.0.1',12)) as current:
        current.headers['Origin']='http://localhost:8000';current.pipeline=adapter
        user=email_user(auth_database);assert login(current,email=user['email']).status_code==200
        current.test_user=user;yield current


def setup(client):
    pid,image,body,draft=graph(client)
    version=confirm(client,pid,draft).json()
    return dict(project_id=pid,image_id=image['id'],criteria_version_id=version['id']),draft


def submit(client,body,key=None):
    return post(client,'/reviews',body,**{'Idempotency-Key':str(key or uuid4())})


def test_atomic_review_admission_background_result_pages_and_export(client,auth_database):
    body,draft=setup(client);key=uuid4();response=submit(client,body,key)
    assert response.status_code==202,response.text
    receipt=response.json();assert receipt['status']=='queued' and response.headers['Location']=='/api/v1/reviews/'+receipt['id']
    response=client.get(response.headers['Location']);assert response.status_code==200,response.text
    detail=response.json();assert detail['status']=='completed',detail
    assert detail['results_complete'] and detail['sealed_at'] and detail['progress']==100
    assert client.pipeline.calls==1
    assert len(detail['results'])==1 and detail['criteria_version']==1
    result=detail['results'][0];assert result['evidence']==[]
    ocr=client.get(detail['ocr']['records_url']).json();assert ocr['availability']=='available' and len(ocr['items'])==1
    assert ocr['items'][0]['bbox_xyxy']==[0,0,4,3]
    assert ocr['items'][0]['external_token_id']=='1' and ocr['items'][0]['external_token_type']=='integer'
    exported=client.get('/api/v1/reviews/'+receipt['id']+'/export');assert exported.status_code==200
    payload=exported.json();assert payload['contract_version']=='cc-review-export-v1' and 'meta' not in payload
    for private in ('worker_token','storage_key','password_hash','synthetic-private'):assert private not in exported.text
    assert exported.headers['X-Content-Type-Options']=='nosniff'
    replay=submit(client,body,key);assert replay.status_code==200 and replay.json()['id']==receipt['id'] and replay.json()['status']=='completed'
    assert client.pipeline.calls==1
    with auth_database[1].connect() as c:
        run=one(c,'review_runs','run_id',UUID(receipt['id']))
        assert run['result_manifest_hash'] and run['active_attempt_id'] is None and run['selected_attempt_id']
        assert c.execute(select(func.count()).select_from(table('stored_files')).where(table('stored_files').c.project_id==UUID(body['project_id']))).scalar_one()==6
    assert auth_database[0].engine.pool.checkedout()==0


@pytest.mark.parametrize('mode',['duplicate','bad_box','exception','missing_item','wrong_item','unknown_evidence'])
def test_invalid_ocr_or_result_fails_without_partial_graph(client,auth_database,mode):
    body,draft=setup(client);client.pipeline.mode=mode
    receipt=submit(client,body).json();detail=client.get('/api/v1/reviews/'+receipt['id']).json()
    assert detail['status']=='failed' and not detail['results_complete'] and detail['overall_verdict'] is None,detail
    assert detail['results']==[] and 'synthetic-private' not in str(detail)
    with auth_database[1].connect() as c:
        run=one(c,'review_runs','run_id',UUID(receipt['id']))
        for name,field in [('item_results','run_id'),('review_attempts','run_id')]:
            if name=='item_results':assert c.execute(select(func.count()).select_from(table(name)).where(table(name).c[field]==run['run_id'])).scalar_one()==0
        assert run['sealed_at'] is None
    assert client.pipeline.calls==1


@pytest.mark.parametrize('mode',['empty','null_confidence','unsupported','structured'])
def test_conservative_null_and_unsupported_projection(client,mode):
    body,draft=setup(client);client.pipeline.mode=mode
    receipt=submit(client,body).json();detail=client.get('/api/v1/reviews/'+receipt['id']).json()
    assert detail['status']=='completed',detail
    if mode=='unsupported':
        assert detail['results'][0]['verdict'] is None and detail['results'][0]['status']=='review' and detail['overall_verdict']=='review'
    if mode=='structured':assert detail['results'][0]['observed_text'] is None
    if mode=='empty':assert all(field['presence_status']!='absent' for field in detail['event_extraction']['fields'])
    if mode=='null_confidence':assert client.get(detail['ocr']['records_url']).json()['items'][0]['confidence'] is None


def test_replay_survives_draft_config_archive_but_requires_current_write_role(client,auth_database):
    body,draft=setup(client);key=uuid4();receipt=submit(client,body,key).json()
    put=dict(expected_revision='1',raw_requirements='바뀐 요구사항',items=draft['items'],source_documents=None)
    assert client.put('/api/v1/projects/'+body['project_id']+'/criteria-drafts/'+draft['id'],json=put,headers=csrf(client)).status_code==200
    client.pipeline.revision='changed-config'
    assert submit(client,body,key).status_code==200
    assert submit(client,body).json()['error']['code']=='CRITERIA_STALE'
    assert client.patch('/api/v1/projects/'+body['project_id'],json={'archived':True},headers=csrf(client)).status_code==200
    assert submit(client,body,key).status_code==200
    other=email_user(auth_database);auth_database[2].set_project_member(UUID(body['project_id']),UUID(other['id']),'owner',True)
    auth_database[2].set_project_member(UUID(body['project_id']),UUID(client.test_user['id']),'viewer',True)
    assert submit(client,body,key).status_code==403
    assert client.get('/api/v1/reviews/'+receipt['id']).status_code==200
    assert client.pipeline.calls==1


def test_retry_gets_new_run_and_operation_key_scope_is_independent(client):
    body,draft=setup(client);key=uuid4();first=submit(client,body,key).json()
    response=post(client,'/reviews/'+first['id']+'/retry',{},**{'Idempotency-Key':str(key)})
    assert response.status_code==202,response.text
    retry=response.json();assert retry['retry_of']==first['id'] and retry['id']!=first['id']
    replay=post(client,'/reviews/'+first['id']+'/retry',{},**{'Idempotency-Key':str(key)})
    assert replay.status_code==200 and replay.json()['id']==retry['id']
    assert client.pipeline.calls==2


@pytest.mark.parametrize('state',['queued','running'])
def test_nonterminal_retry_rejected_and_claim_is_single_owner(client,state):
    body,draft=setup(client);who=client.app.state.auth.current(client.cookies['baro_session'],developer_allowed=True)
    run,_=client.app.state.reviews.admit(who,True,body,uuid4(),str(uuid4()))
    if state=='running':
        with ThreadPoolExecutor(max_workers=2) as workers:claims=list(workers.map(lambda _:client.app.state.reviews.claim(UUID(run['id'])),range(2)))
        assert sum(claim is not None for claim in claims)==1
    response=post(client,'/reviews/'+run['id']+'/retry',{},**{'Idempotency-Key':str(uuid4())})
    assert response.status_code==409 and response.json()['error']['code']=='REVIEW_NOT_TERMINAL'
    assert client.pipeline.calls==0


def test_concurrent_admission_same_key_one_run_and_same_input_config(client):
    body,draft=setup(client);who=client.app.state.auth.current(client.cookies['baro_session'],developer_allowed=True);key=uuid4()
    with ThreadPoolExecutor(max_workers=2) as workers:
        values=list(workers.map(lambda _:client.app.state.reviews.admit(who,True,body,key,str(uuid4())),range(2)))
    assert len({value[0]['id'] for value in values})==1 and sorted(value[1] for value in values)==[False,True]
    run=UUID(values[0][0]['id'])
    with ThreadPoolExecutor(max_workers=2) as workers:list(workers.map(lambda _:client.app.state.review_executor.execute(run),range(2)))
    assert client.pipeline.calls==1 and client.get('/api/v1/reviews/'+str(run)).json()['status']=='completed'


@pytest.mark.parametrize('field',['project_id','image_id','criteria_version_id'])
def test_other_project_ids_do_not_form_a_run(client,field):
    body,draft=setup(client);other,_=setup(client);wrong=dict(body,**{field:other[field]})
    response=submit(client,wrong);assert response.status_code==404,response.text
    assert client.pipeline.calls==0


def test_same_key_different_body_and_other_actor(client,auth_database):
    body,draft=setup(client);key=uuid4();first=submit(client,body,key).json()
    other_body,_=setup(client)
    assert submit(client,other_body,key).json()['error']['code']=='IDEMPOTENCY_KEY_REUSED'
    user=email_user(auth_database);auth_database[2].set_project_member(UUID(body['project_id']),UUID(user['id']),'editor',True)
    assert login(client,email=user['email']).status_code==200
    second=submit(client,body,key);assert second.status_code==202 and second.json()['id']!=first['id']


@pytest.mark.parametrize('kind',['expired','disabled','outsider'])
def test_current_identity_and_membership_control_history(client,auth_database,kind):
    body,draft=setup(client);receipt=submit(client,body).json()
    if kind=='disabled':
        other=email_user(auth_database);auth_database[2].set_project_member(UUID(body['project_id']),UUID(other['id']),'owner',True)
        auth_database[2].set_user_active(UUID(client.test_user['id']),False)
    elif kind=='outsider':
        other=email_user(auth_database);assert login(client,email=other['email']).status_code==200
    else:
        from backend.app.services.auth import token_hash
        with auth_database[1].begin() as c:c.exec_driver_sql("UPDATE public.user_sessions SET created_at=clock_timestamp()-interval '9 hours',expires_at=clock_timestamp()-interval '1 hour' WHERE token_hash=%s",(token_hash(client.cookies['baro_session']),))
    for path in ('','/ocr-records','/export'):
        assert client.get('/api/v1/reviews/'+receipt['id']+path).status_code==(404 if kind=='outsider' else 401)


@pytest.mark.parametrize('query',['unknown=1','limit=0','limit=101','limit=1&limit=2','status=bad','cursor=bad'])
def test_review_list_contract(client,query):
    body,draft=setup(client)
    assert client.get('/api/v1/projects/'+body['project_id']+'/reviews?'+query).status_code in (400,422)


def test_list_evidence_pagination_and_cross_run_sources(client):
    body,draft=setup(client)
    # Make the criterion match a recognized literal so actual conservative rules create evidence.
    put=dict(expected_revision='1',raw_requirements='예시 축제',items=[dict(id='selected',text='행사명: 예시 축제',type='information',source='원문',selected=True)],source_documents=None)
    changed=client.put('/api/v1/projects/'+body['project_id']+'/criteria-drafts/'+draft['id'],json=put,headers=csrf(client)).json()
    version=confirm(client,body['project_id'],changed).json();body['criteria_version_id']=version['id']
    ids=[submit(client,body).json()['id'] for _ in range(3)]
    first=client.get('/api/v1/projects/'+body['project_id']+'/reviews?limit=2').json()
    second=client.get('/api/v1/projects/'+body['project_id']+'/reviews',params={'limit':2,'cursor':first['next_cursor']}).json()
    assert len({row['id'] for row in first['items']+second['items']})==3
    detail=client.get('/api/v1/reviews/'+ids[0]).json();result=detail['results'][0]
    assert result['evidence_count']==1 and not result['evidence_complete']
    page=client.get(result['evidence_url']).json();assert page['evidence_count']==1 and page['items'][0]['token_ids']
    assert client.get('/api/v1/reviews/'+ids[1]+'/results/'+result['result_id']+'/evidence').status_code==404
    assert client.get('/api/v1/projects/'+body['project_id']+'/reviews?image_id='+setup(client)[0]['image_id']).status_code==404


def test_result_commit_loss_and_same_output_restore_without_reinference(client,monkeypatch):
    from backend.app.persistence.database import PersistenceError
    body,draft=setup(client);executor=client.app.state.review_executor;original=executor.persist
    def lost(*args):
        original(*args)
        raise PersistenceError('DB_COMMIT_OUTCOME_UNKNOWN','synthetic',outcome='commit_unknown')
    monkeypatch.setattr(executor,'persist',lost)
    receipt=submit(client,body).json();detail=client.get('/api/v1/reviews/'+receipt['id']).json()
    assert detail['status']=='completed' and client.pipeline.calls==1
    journals=[value for value in client.app.state.storage.journals() if value['operation']=='review_result']
    assert journals[-1]['db_outcome']=='committed'
    assert executor.resolve(journals[-1])=='committed'
    changed=dict(journals[-1],result_manifest_hash='0'*64);assert executor.resolve(changed)=='conflict'


def test_mid_result_db_error_rolls_back_entire_output_graph(client,auth_database):
    from sqlalchemy import event
    body,draft=setup(client);engine=auth_database[0].engine;fired=[]
    def fail(c,cursor,statement,parameters,context,many):
        if 'INSERT INTO public.item_results ' in statement and not fired:fired.append(True);cursor.execute('SELECT 1/0')
    event.listen(engine,'before_cursor_execute',fail)
    try:receipt=submit(client,body).json()
    finally:event.remove(engine,'before_cursor_execute',fail)
    detail=client.get('/api/v1/reviews/'+receipt['id']).json();assert fired and detail['status']=='failed',detail
    with auth_database[1].connect() as c:
        for name in ('ocr_runs','poster_extractions','item_results','evidence_records'):
            assert c.execute(select(func.count()).select_from(table(name)).where(table(name).c.asset_version_id==UUID(body['image_id']))).scalar_one()==0
    intent=[value for value in client.app.state.storage.journals() if value['operation']=='review_result'][-1]
    assert client.app.state.review_executor.resolve(intent)=='not_committed'
    for file in intent['files']:
        with client.app.state.storage.verified(file['storage_key'],int(file['byte_size']),file['sha256']):pass


def test_stale_token_cannot_store_and_late_output_cannot_replace_sealed_result(client):
    body,draft=setup(client);service=client.app.state.reviews;executor=client.app.state.review_executor
    who=client.app.state.auth.current(client.cookies['baro_session'],developer_allowed=True)
    accepted,_=service.admit(who,True,body,uuid4(),str(uuid4()));run,attempt,token=service.claim(UUID(accepted['id']))
    plan,intent=executor.prepare(run,attempt,token)
    with client.app.state.storage.lock('gate',shared=True):
        for item in intent['files']:client.app.state.storage.publish(intent['operation_id'],item)
        with pytest.raises(AuthError):executor.persist(plan,uuid4())
        executor.persist(plan,token)
        with pytest.raises(AuthError):executor.persist(plan,token)
    assert client.get('/api/v1/reviews/'+accepted['id']).json()['status']=='completed' and client.pipeline.calls==1


def test_resize_ocr_and_checker_hold_no_request_database_connection(client,auth_database,monkeypatch):
    body,draft=setup(client);original=client.pipeline.recognize;check=client.pipeline.check
    def recognize(*args):assert auth_database[0].engine.pool.checkedout()==0;return original(*args)
    def checked(*args):assert auth_database[0].engine.pool.checkedout()==0;return check(*args)
    monkeypatch.setattr(client.pipeline,'recognize',recognize);monkeypatch.setattr(client.pipeline,'check',checked)
    receipt=submit(client,body).json();assert client.get('/api/v1/reviews/'+receipt['id']).json()['status']=='completed'


def test_response_and_export_size_caps_are_enforced_before_stream(client,monkeypatch):
    import backend.app.routes.v1.reviews as routes
    body,draft=setup(client);receipt=submit(client,body).json()
    monkeypatch.setattr(routes,'MAX_RESPONSE',1)
    response=client.get('/api/v1/reviews/'+receipt['id']);assert response.status_code==503 and response.json()['error']['fields']['export_url'].endswith('/export')
    monkeypatch.setattr(routes,'MAX_EXPORT',1)
    response=client.get('/api/v1/reviews/'+receipt['id']+'/export');assert response.status_code==422 and response.json()['error']['code']=='EXPORT_TOO_LARGE'


def test_database_service_lock_rejects_different_root_then_restart_closes_only_background_jobs(client,auth_database,tmp_path):
    root=tmp_path.resolve()/'other-root';LocalStorage.initialize(root)
    with pytest.raises(AuthError) as error:
        with TestClient(create_app(settings=replace(client.app.state.settings,storage_root=root),engine=auth_database[0].engine,review_adapter=SyntheticPipeline())):pass
    assert error.value.code=='BACKGROUND_SERVICE_BUSY'
    body,draft=setup(client);service=client.app.state.reviews;who=client.app.state.auth.current(client.cookies['baro_session'],developer_allowed=True)
    queued,_=service.admit(who,True,body,uuid4(),str(uuid4()))
    running,_=service.admit(who,True,body,uuid4(),str(uuid4()));service.claim(UUID(running['id']))
    completed=submit(client,body).json()
    client.app.state.review_leader.close();client.app.state.storage.close()
    with TestClient(create_app(settings=client.app.state.settings,engine=auth_database[0].engine,review_adapter=SyntheticPipeline()),base_url='http://localhost:8000',client=('127.0.0.1',43)) as restarted:
        restarted.cookies.update(client.cookies)
        for resource,code in ((queued,'RESTART_BEFORE_START'),(running,'RESTART_DURING_EXECUTION')):
            detail=restarted.get('/api/v1/reviews/'+resource['id']).json();assert detail['status']=='failed' and detail['error']['code']==code
        assert restarted.get('/api/v1/reviews/'+completed['id']).json()['status']=='completed'
        assert restarted.app.state.reviews.adapter.calls==0


def test_model_files_configuration_does_not_claim_inference_health_before_success(client):
    before=client.get('/api/v1/health').json()['checks'];assert before['model']['state']=='ready' and before['ocr_runtime']['state']=='unknown'
    body,draft=setup(client);submit(client,body)
    assert client.get('/api/v1/health').json()['checks']['ocr_runtime']['state']=='ready'
    client.pipeline.revision='new-synthetic-revision'
    assert client.get('/api/v1/health').json()['checks']['ocr_runtime']['state']=='unknown'


def test_explicit_disabled_profile_keeps_history_and_rejects_new_runtime(client,auth_database,tmp_path):
    body,draft=setup(client);accepted=submit(client,body).json()
    # Separate root intentionally cannot supply existing image bytes; metadata/replay needs none.
    root=tmp_path.resolve()/'disabled-root';LocalStorage.initialize(root)
    with TestClient(create_app(settings=replace(client.app.state.settings,storage_root=root),engine=auth_database[0].engine),base_url='http://localhost:8000',client=('127.0.0.1',43)) as disabled:
        disabled.cookies.update(client.cookies);disabled.headers['Origin']='http://localhost:8000'
        assert disabled.get('/api/v1/reviews/'+accepted['id']).status_code==200
        assert submit(disabled,body).status_code==503


def test_http_strict_create_retry_csrf_and_content_encoding(client):
    body,draft=setup(client)
    assert client.post('/api/v1/reviews',json=body,headers={'Idempotency-Key':str(uuid4())}).status_code==403
    assert post(client,'/reviews',dict(body,model='unexpected'),**{'Idempotency-Key':str(uuid4())}).status_code==422
    assert post(client,'/reviews',body).status_code==422
    assert post(client,'/reviews',body,**{'Idempotency-Key':str(uuid4()),'Content-Encoding':'gzip'}).status_code==415
    accepted=submit(client,body).json()
    assert post(client,'/reviews/'+accepted['id']+'/retry',{'extra':True},**{'Idempotency-Key':str(uuid4())}).status_code==422


def test_manifest_fingerprints_include_actual_output_values_and_persisted_graph(client,auth_database):
    from backend.app.hashing.canonical import digest
    body,draft=setup(client);accepted=submit(client,body).json()
    journal=[value for value in client.app.state.storage.journals() if value['operation']=='review_result'][-1]
    with client.app.state.storage.read('staging/'+journal['operation_id']+'/plan.json') as stream:plan=json.load(stream)
    manifest=dict(run_id=plan['run_id'],selected_attempt_id=plan['attempt_id'],execution_status='completed',overall_verdict=plan['overall_verdict'],
        aggregation_policy_version='all_selected_v1',input_manifest_hash=plan['input_manifest_hash'],
        ocr=dict(ocr_run_id=plan['ocr_id'],ocr_input_sha256=plan['files'][0]['sha256'],source_json_sha256=plan['files'][1]['sha256'],records=plan['records']),
        extraction=dict(extraction_id=plan['extraction_id'],source_json_sha256=plan['files'][2]['sha256'],observations=plan['observations']),
        checker_source_sha256=plan['files'][3]['sha256'],items=plan['items'])
    assert digest('review_result',manifest,'cc-manifest-jcs-v1')==plan['result_manifest_hash']==journal['result_manifest_hash']
    with auth_database[1].connect() as c:
        run=one(c,'review_runs','run_id',UUID(accepted['id']));assert run['result_manifest_hash']==plan['result_manifest_hash']
        rows=c.execute(select(table('item_results')).where(table('item_results').c.run_id==run['run_id'])).mappings().all()
        assert len(rows)==len(plan['items'])
        for row,item in zip(rows,plan['items']):
            assert all(row[key]==item[key] for key in ('verdict','reason','observed_value'))
    manifest['items'][0]['reason']='changed output reason'
    assert digest('review_result',manifest,'cc-manifest-jcs-v1')!=plan['result_manifest_hash']


def test_admission_capacity_is_bounded_without_inference(client):
    body,draft=setup(client);service=client.app.state.reviews;who=client.app.state.auth.current(client.cookies['baro_session'],developer_allowed=True)
    resources=[service.admit(who,True,body,uuid4(),str(uuid4()))[0] for _ in range(16)]
    with pytest.raises(AuthError) as error:service.admit(who,True,body,uuid4(),str(uuid4()))
    assert error.value.code=='REVIEW_CAPACITY' and client.pipeline.calls==0
    for resource in resources:service.fail(UUID(resource['id']),code='SYNTHETIC_TEST_CLEANUP')


def test_loss_of_dedicated_service_session_is_not_automatically_reacquired(client,auth_database):
    body,draft=setup(client);leader=client.app.state.review_leader
    with unit_of_work(auth_database[0].engine) as c:
        assert c.exec_driver_sql('SELECT pg_terminate_backend(%s)',(leader.pid,)).scalar_one()
    with pytest.raises(AuthError) as error:leader.check()
    assert error.value.code=='BACKGROUND_AUTHORITY_LOST'
    assert submit(client,body).status_code==503 and client.pipeline.calls==0


def test_admission_commit_acknowledgement_loss_returns_existing_resource(client,auth_database,monkeypatch):
    from contextlib import contextmanager
    from sqlalchemy import event
    from backend.app.persistence.database import PersistenceError
    import backend.app.services.reviews as module
    body,draft=setup(client);original=module.unit_of_work;armed=[False];lost=[False]
    def watch(c,cursor,statement,parameters,context,many):
        if 'INSERT INTO public.review_runs ' in statement:armed[0]=True
    @contextmanager
    def transaction(engine):
        with original(engine) as c:yield c
        if armed[0] and not lost[0]:
            lost[0]=True
            raise PersistenceError('DB_COMMIT_OUTCOME_UNKNOWN','synthetic',outcome='commit_unknown')
    event.listen(auth_database[0].engine,'before_cursor_execute',watch);monkeypatch.setattr(module,'unit_of_work',transaction)
    try:
        key=uuid4();response=submit(client,body,key)
        assert response.status_code==202,response.text
        assert submit(client,body,key).json()['id']==response.json()['id'] and lost[0]
        assert client.get('/api/v1/reviews/'+response.json()['id']).json()['status']=='completed' and client.pipeline.calls==1
    finally:event.remove(auth_database[0].engine,'before_cursor_execute',watch)


def test_unknown_result_commit_preserves_files_and_waits_for_reconciliation(client,monkeypatch):
    from backend.app.persistence.database import PersistenceError
    body,draft=setup(client);executor=client.app.state.review_executor;resolve=executor.resolve
    with monkeypatch.context() as patch:
        def unknown(*args):raise PersistenceError('DB_COMMIT_OUTCOME_UNKNOWN','synthetic',outcome='commit_unknown')
        patch.setattr(executor,'persist',unknown);patch.setattr(executor,'resolve',lambda intent:'unresolved')
        accepted=submit(client,body).json()
        assert client.get('/api/v1/reviews/'+accepted['id']).json()['status']=='running'
    intent=[value for value in client.app.state.storage.journals() if value['operation']=='review_result'][-1]
    assert intent['db_outcome']=='unresolved'
    for file in intent['files']:
        with client.app.state.storage.verified(file['storage_key'],int(file['byte_size']),file['sha256']):pass
    assert resolve(intent)=='not_committed'
    executor.recover();client.app.state.reviews.recover_runs()
    assert client.get('/api/v1/reviews/'+accepted['id']).json()['status']=='failed' and client.pipeline.calls==1


def test_partial_file_publication_failure_preserves_existing_input_and_no_partial_db(client,auth_database,monkeypatch):
    from backend.app.storage.local import StorageError
    body,draft=setup(client);storage=client.app.state.storage;publish=storage.publish;calls=[]
    def interrupted(*args):
        calls.append(True)
        if len(calls)==2:raise StorageError()
        return publish(*args)
    monkeypatch.setattr(storage,'publish',interrupted)
    accepted=submit(client,body).json();assert client.get('/api/v1/reviews/'+accepted['id']).json()['status']=='failed'
    with auth_database[1].connect() as c:
        assert c.execute(select(func.count()).select_from(table('stored_files')).where(table('stored_files').c.project_id==UUID(body['project_id']))).scalar_one()==2
    intent=[value for value in storage.journals() if value['operation']=='review_result'][-1]
    first=intent['files'][0]
    with storage.verified(first['storage_key'],int(first['byte_size']),first['sha256']):pass


def test_project_name_and_input_hash_are_snapshots_while_accepted_work_finishes(client):
    body,draft=setup(client);service=client.app.state.reviews;who=client.app.state.auth.current(client.cookies['baro_session'],developer_allowed=True)
    accepted,_=service.admit(who,True,body,uuid4(),str(uuid4()))
    old_name=client.get('/api/v1/projects/'+body['project_id']).json()['name']
    assert client.patch('/api/v1/projects/'+body['project_id'],json={'name':'나중 이름','archived':True},headers=csrf(client)).status_code==200
    put=dict(expected_revision='1',raw_requirements='다른 요구사항',items=draft['items'],source_documents=None)
    # Archival blocks edits/new admission but does not invalidate accepted work.
    assert client.put('/api/v1/projects/'+body['project_id']+'/criteria-drafts/'+draft['id'],json=put,headers=csrf(client)).status_code==409
    client.app.state.review_executor.execute(UUID(accepted['id']))
    detail=client.get('/api/v1/reviews/'+accepted['id']).json();assert detail['status']=='completed' and detail['project_name']==old_name


def test_imported_history_is_unconfirmed_local_result_and_not_retryable(client,auth_database):
    from sqlalchemy import insert
    body,draft=setup(client);identifier=uuid4()
    with auth_database[1].begin() as c:
        c.execute(insert(table('review_runs')).values(run_id=identifier,project_id=UUID(body['project_id']),asset_version_id=UUID(body['image_id']),
            criteria_version_id=UUID(body['criteria_version_id']),origin_kind='legacy_result',source_snapshot_jsonb={'criteria_version':'legacy-version'},source_status='completed',
            execution_status='imported',orchestration_mode='imported',executor_kind='imported',result_manifest_hash='a'*64,manifest_hash_version='fixture-import',sealed_at=func.clock_timestamp()))
    response=client.get('/api/v1/reviews/'+str(identifier));assert response.status_code==200,response.text
    detail=response.json();assert detail['status']=='imported' and detail['overall_verdict'] is None and not detail['results_complete']
    retry=post(client,'/reviews/'+str(identifier)+'/retry',{},**{'Idempotency-Key':str(uuid4())})
    assert retry.status_code==409 and retry.json()['error']['code']=='SOURCE_NOT_RETRYABLE'


def test_background_recovery_never_claims_or_closes_postgres_worker_rows(client,auth_database):
    from sqlalchemy import insert
    body,draft=setup(client);service=client.app.state.reviews;who=client.app.state.auth.current(client.cookies['baro_session'],developer_allowed=True)
    accepted,_=service.admit(who,True,body,uuid4(),str(uuid4()));worker_id=uuid4()
    with auth_database[1].begin() as c:
        original=one(c,'review_runs','run_id',UUID(accepted['id']))
        original.update(run_id=worker_id,idempotency_key=uuid4(),executor_kind='postgres_worker')
        c.execute(insert(table('review_runs')).values(**original))
    service.recover_runs();client.app.state.review_executor.execute(worker_id)
    assert client.get('/api/v1/reviews/'+str(worker_id)).json()['status']=='queued'
    assert client.get('/api/v1/reviews/'+accepted['id']).json()['status']=='failed' and client.pipeline.calls==0


def test_duplicate_delivery_does_not_wait_for_the_busy_inference_slot(client,monkeypatch):
    import threading
    body,draft=setup(client);service=client.app.state.reviews;who=client.app.state.auth.current(client.cookies['baro_session'],developer_allowed=True)
    accepted,_=service.admit(who,True,body,uuid4(),str(uuid4()));run_id=UUID(accepted['id'])
    entered=threading.Event();release=threading.Event();original=client.pipeline.recognize
    def blocked(*args):
        entered.set();assert release.wait(timeout=5);return original(*args)
    monkeypatch.setattr(client.pipeline,'recognize',blocked)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first=workers.submit(client.app.state.review_executor.execute,run_id)
        try:
            assert entered.wait(timeout=3)
            second=workers.submit(client.app.state.review_executor.execute,run_id)
            second.result(timeout=1)
        finally:release.set()
        first.result(timeout=5)
    assert client.pipeline.calls==1
