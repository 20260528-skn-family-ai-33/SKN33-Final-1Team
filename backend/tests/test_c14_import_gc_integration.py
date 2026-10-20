"""Real PostgreSQL immutable import, file GC, availability/repair and management CLI."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID,uuid4
import hashlib,json,os,subprocess,sys,time,threading
import pytest
from sqlalchemy import select,insert,update,func
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user
from backend.tests.test_c09_business_integration import project
from backend.tests.test_c10_reviews_integration import submit
from backend.tests.c14_helpers import api,source,owner_values,private_env
from backend.tests.c12_helpers import clean_environment
from backend.app.storage.local import LocalStorage,key,operation_key,StorageError
from backend.app.management.imports import Imports
from backend.app.management.files import FileOperations
from backend.app.management.io import OpsError,Tree
from backend.app.management.maintenance import session_lock,MAINTENANCE_KEY
from backend.app.persistence.transaction import unit_of_work
from backend.app.persistence.database import PersistenceError
from backend.app.services.business import table,one

pytestmark=pytest.mark.db_integration


@pytest.fixture(scope='module')
def env(auth_database,tmp_path_factory):
    root=tmp_path_factory.mktemp('c14-import').resolve()/'files';LocalStorage.initialize(root)
    settings=replace(auth_database[3],storage_root=root,review_executor='postgres_worker')
    user=email_user(auth_database)
    return SimpleNamespace(db=auth_database[0],owner=auth_database[1],manager=auth_database[2],root=root,settings=settings,user=user)


@pytest.fixture
def context(env,tmp_path):
    with api(env) as client:
        pid=UUID(project(client));storage=LocalStorage(env.root);imports=Imports(env.owner,storage)
        current=SimpleNamespace(env=env,client=client,pid=pid,storage=storage,imports=imports,source=source(tmp_path/'source'),allowed=tmp_path,actor=UUID(env.user['id']),orphans=[])
        try:yield current
        finally:
            for op in current.orphans:
                journal=env.root/'operations'/op/'journal.json'
                if journal.exists():journal.unlink()  # Test-only synthetic intent; payloads remain preserved.
            storage.close()


def plan(ctx,**kw):
    return ctx.imports.dry_run(ctx.source,ctx.allowed,project_id=ctx.pid,actor_id=ctx.actor,namespace='synthetic-legacy/v1',display_name='합성 수입',**kw)


def apply(ctx,p,**kw):return ctx.imports.apply(p['operation_id'],ctx.source,ctx.allowed,actor_id=ctx.actor,**kw)


def counts(ctx):
    with unit_of_work(ctx.env.owner) as c:
        return {name:c.execute(select(func.count()).select_from(table(name)).where(table(name).c.project_id==ctx.pid)).scalar_one() for name in ('import_packages','assets','asset_versions','criteria_versions','review_runs','item_results','stored_files','audit_events')}


def test_dry_run_has_no_business_writes_and_same_fingerprint_with_new_ids(context):
    ctx=context;before=counts(ctx)
    with Tree(ctx.source) as tree:raw_before=tree.inventory()
    first=plan(ctx);second=plan(ctx)
    assert first['state']=='ready' and first['package_fingerprint']==second['package_fingerprint']
    assert first['operation_id']!=second['operation_id'] and first['resources']!=second['resources'] and counts(ctx)==before
    with Tree(ctx.source) as tree:assert tree.inventory()==raw_before


@pytest.mark.parametrize('external',(False,True))
def test_import_preserves_source_without_creating_local_verdict_or_requester(context,external):
    ctx=context
    if external:(ctx.source/'manifest.json').write_text(json.dumps(dict(contract_version='cc-import-source-v1',files=dict(snapshot='snapshot.json',result='result.json',image='original.bin'))))
    p=plan(ctx);result=apply(ctx,p);assert result['state']=='completed'
    ids=p['resources'];data=ctx.client.get('/api/v1/reviews/'+ids['run_id']).json()
    assert data['status']=='imported' and data['overall_verdict'] is None and data['results_complete']
    assert data['results'][0]['verdict'] is None and data['results'][0]['processing_status']=='imported'
    assert data['ocr'] is None and data['results'][0]['evidence_count']==0
    with unit_of_work(ctx.env.owner) as c:
        run=one(c,'review_runs','run_id',UUID(ids['run_id']));version=one(c,'criteria_versions','criteria_version_id',UUID(ids['criteria_version_id']));attempt=one(c,'review_attempts','attempt_id',UUID(ids['attempt_id']))
        assert run['requested_by'] is None and run['started_at'] is None and run['finished_at'] is None
        assert run['source_snapshot_jsonb']['requester']=='local-admin' and version['confirmation_state']=='unconfirmed'
        assert version['confirmed_by'] is None and attempt['worker_token'] is None and attempt['started_at'] is None
        assert attempt['remote_job_id'] is None and attempt['execution_status']=='imported'
    response=submit(ctx.client,dict(project_id=str(ctx.pid),image_id=ids['image_id'],criteria_version_id=ids['criteria_version_id']))
    assert response.status_code==409 and response.json()['error']['code']=='CRITERIA_NOT_CONFIRMED'
    before=counts(ctx);assert apply(ctx,p)['replayed'] and counts(ctx)==before
    another=plan(ctx);assert apply(ctx,another)['resources']==ids and counts(ctx)==before


def test_partial_source_results_remain_partial_without_invented_passes(context):
    ctx=context;data=json.loads((ctx.source/'snapshot.json').read_text());data['criteria'].append(dict(id='two',text='장소',type='information'));(ctx.source/'snapshot.json').write_text(json.dumps(data))
    p=plan(ctx);assert not p['preview']['results_complete'];apply(ctx,p)
    detail=ctx.client.get('/api/v1/reviews/'+p['resources']['run_id']).json()
    assert not detail['results_complete'] and len(detail['criteria'])==2 and len(detail['results'])==1


@pytest.mark.parametrize('kind',('missing_image','missing_criteria','precision','provider_hash','unknown_result','multi_poster'))
def test_ambiguous_source_is_intake_only_needs_review(context,kind):
    ctx=context
    if kind=='missing_image':(ctx.source/'original.bin').unlink()
    if kind=='missing_criteria':(ctx.source/'snapshot.json').write_text('{}')
    if kind=='precision':(ctx.source/'snapshot.json').write_text('{"criteria_version":9007199254740993}')
    if kind=='provider_hash':(ctx.source/'manifest.json').write_text(json.dumps(dict(contract_version='cc-import-source-v1',files=dict(snapshot='snapshot.json',result='result.json',image='original.bin'),sha256={'original.bin':'0'*64})))
    if kind=='unknown_result':
        value=json.loads((ctx.source/'result.json').read_text());value['results'][0]['criterion_id']='unknown';(ctx.source/'result.json').write_text(json.dumps(value))
    if kind=='multi_poster':(ctx.source/'second.png').write_bytes((ctx.source/'original.bin').read_bytes())
    p=plan(ctx);assert p['state']=='needs_review'
    before=counts(ctx);result=apply(ctx,p);after=counts(ctx)
    assert result['state']=='needs_review' and after['import_packages']==before['import_packages']+1
    for name in ('assets','asset_versions','criteria_versions','review_runs','item_results','stored_files'):assert before[name]==after[name]
    assert (ctx.env.root/'intake'/p['operation_id']/'sources').is_dir()


def test_apply_rejects_source_changes_and_staging_changes(context):
    ctx=context;p=plan(ctx);original=(ctx.source/'result.json').read_bytes();(ctx.source/'result.json').write_bytes(original+b' ')
    with pytest.raises(OpsError,match='INPUT_CHANGED'):apply(ctx,p)
    assert counts(ctx)['import_packages']==0
    (ctx.source/'result.json').write_bytes(original)
    intake=ctx.env.root/'intake'/p['operation_id']/'sources/result.json';intake.write_bytes(original+b' ')
    with pytest.raises(OpsError,match='INPUT_CHANGED'):apply(ctx,p)
    assert counts(ctx)['review_runs']==0


def test_frozen_plan_resource_ids_cannot_be_edited_behind_the_fingerprint(context):
    ctx=context;p=plan(ctx);path=ctx.env.root/'intake'/p['operation_id']/'plan.json';value=json.loads(path.read_text());value['resources']['run_id']=str(uuid4());path.write_text(json.dumps(value))
    with pytest.raises(OpsError,match='IMPORT_PLAN_CHANGED'):apply(ctx,p)
    assert counts(ctx)['import_packages']==0


def test_same_package_concurrent_apply_and_confirmed_rollback_revalidation(context,monkeypatch):
    ctx=context;p=plan(ctx);entered=threading.Event();release=threading.Event();real=ctx.imports.complete
    def blocked(*args):entered.set();assert release.wait(5);return real(*args)
    monkeypatch.setattr(ctx.imports,'complete',blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(apply,ctx,p)
        try:
            assert entered.wait(2)
            with pytest.raises(OpsError,match='MANAGEMENT_BUSY'):apply(ctx,p)
        finally:release.set()
        assert future.result(timeout=6)['state']=='completed'
    assert counts(ctx)['import_packages']==1 and counts(ctx)['review_runs']==1


@pytest.mark.parametrize('fault',('rollback','ack_loss'))
def test_graph_transaction_is_atomic_and_unknown_commit_is_reconciled(context,monkeypatch,fault):
    import backend.app.management.imports as module
    ctx=context;p=plan(ctx);real=module.unit_of_work;injected=False
    @contextmanager
    def faulty(*args,**kw):
        nonlocal injected
        trigger=kw.get('import_operation') and not injected
        with real(*args,**kw) as c:
            yield c
            if trigger and fault=='rollback':injected=True;c.exec_driver_sql('SELECT 1/0')
        if trigger and fault=='ack_loss':
            injected=True;raise PersistenceError('DB_COMMIT_OUTCOME_UNKNOWN','synthetic acknowledgement loss',outcome='commit_unknown')
    with monkeypatch.context() as local:
        local.setattr(module,'unit_of_work',faulty)
        if fault=='rollback':
            with pytest.raises(PersistenceError):apply(ctx,p)
            assert counts(ctx)['review_runs']==0 and counts(ctx)['stored_files']==0
        else:assert apply(ctx,p)['state']=='completed'
    if fault=='rollback':assert apply(ctx,p,revalidate=True)['state']=='completed'
    assert counts(ctx)['review_runs']==1 and counts(ctx)['criteria_versions']==1


def orphan(ctx):
    op=str(uuid4());files=[]
    for role in ('original_image','display_image'):
        file_id=str(uuid4());raw=b'synthetic unreferenced payload'
        value=dict(file_id=file_id,project_id=str(ctx.pid),file_role=role,storage_key=key(ctx.pid,file_id),byte_size=str(len(raw)),sha256=hashlib.sha256(raw).hexdigest())
        ctx.storage.write_new('staging/'+op+'/'+file_id,raw);ctx.storage.publish(op,value);files.append(value)
    intent=dict(contract_version='cc-storage-journal-v1',operation='upload_image',operation_id=op,project_id=str(ctx.pid),asset_id=str(uuid4()),image_id=str(uuid4()),
        original_frame_id=str(uuid4()),display_frame_id=str(uuid4()),files=files,phase='finalized',db_outcome='unconfirmed')
    ctx.storage.journal(intent)
    ctx.orphans.append(op)
    for path in [ctx.env.root/'operations'/op/'journal.json',*[ctx.env.root/value['storage_key'] for value in files]]:os.utime(path,(time.time()-25*3600,time.time()-25*3600))
    return intent


def decision(report,op):return next(item['decision'] for item in report['operations'] if item['operation_id']==op)


def test_gc_dry_run_quarantine_and_seven_day_delete_are_separate(context):
    ctx=context;intent=orphan(ctx);op=intent['operation_id'];gc=FileOperations(ctx.env.owner,ctx.storage)
    assert decision(gc.collect(),op)=='eligible_for_quarantine'
    assert all((ctx.env.root/item['storage_key']).exists() for item in intent['files'])
    assert decision(gc.collect(apply=True),op)=='quarantined'
    assert decision(gc.collect(apply=True,delete=True),op)=='defer_quarantine_grace'
    assert decision(gc.collect(apply=True,delete=True,now=time.time()+8*86400),op)=='deleted'
    assert all(not (ctx.env.root/'quarantine'/op/item['file_id']).exists() for item in intent['files'])


@pytest.mark.parametrize('phase',('quarantine_link','partial_delete'))
def test_gc_interruption_resumes_only_its_recorded_owned_files(context,monkeypatch,phase):
    from backend.app.management import files as module
    ctx=context;intent=orphan(ctx);op=intent['operation_id'];gc=FileOperations(ctx.env.owner,ctx.storage)
    if phase=='quarantine_link':
        def crash(storage,source,target):
            sp,_,sn=source.rpartition('/');tp,_,tn=target.rpartition('/')
            with storage.directory(sp) as src,storage.directory(tp,create=True) as dst:os.link(sn,tn,src_dir_fd=src,dst_dir_fd=dst,follow_symlinks=False)
            raise OpsError('SYNTHETIC_INTERRUPTION')
        with monkeypatch.context() as scoped:
            scoped.setattr(module,'move',crash)
            with pytest.raises(OpsError):gc.collect(apply=True)
        assert decision(gc.collect(apply=True),op)=='quarantined'
    else:
        gc.collect(apply=True);real=os.unlink;first=intent['files'][0]['file_id']
        def crash(name,*args,**kw):
            real(name,*args,**kw)
            if name==first:raise OSError('Synthetic interruption after owned unlink')
        with monkeypatch.context() as scoped:
            scoped.setattr(os,'unlink',crash)
            with pytest.raises(OSError):gc.collect(apply=True,delete=True,now=time.time()+8*86400)
        assert decision(gc.collect(apply=True,delete=True,now=time.time()+8*86400),op)=='deleted'


@pytest.mark.parametrize('kind',('operation','database','gate','registered_missing','no_journal'))
def test_gc_preserves_busy_registered_and_unowned_files(context,kind):
    ctx=context;intent=orphan(ctx);op=intent['operation_id'];gc=FileOperations(ctx.env.owner,ctx.storage)
    if kind=='operation':
        with ctx.storage.lock('operation-'+op):assert decision(gc.collect(apply=True),op)=='busy_operation'
    if kind=='database':
        with unit_of_work(ctx.env.owner) as c:
            c.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)',(operation_key(op),));assert decision(gc.collect(apply=True),op)=='busy_database'
    if kind=='gate':
        with ctx.storage.lock('gate',shared=True):
            with pytest.raises(StorageError):gc.collect(apply=True)
    if kind=='registered_missing':
        value=intent['files'][0]
        with unit_of_work(ctx.env.owner) as c:c.execute(insert(table('stored_files')).values(file_id=UUID(value['file_id']),project_id=ctx.pid,file_role='source_json',storage_key=value['storage_key'],byte_size=int(value['byte_size']),sha256=value['sha256'],state='missing'))
        assert decision(gc.collect(apply=True),op)=='keep_registered'
    if kind=='no_journal':
        (ctx.env.root/'operations'/op/'journal.json').unlink();assert op not in [item['operation_id'] for item in gc.collect(apply=True)['operations']]
    assert all((ctx.env.root/item['storage_key']).exists() for item in intent['files'])


def test_missing_file_503_and_exact_byte_repair_preserve_image_and_hash(context):
    ctx=context;p=plan(ctx);apply(ctx,p);image_id=p['resources']['image_id']
    data=ctx.client.get('/api/v1/images/'+image_id).json();url=data['url']
    with unit_of_work(ctx.env.owner) as c:row=one(c,'stored_files','file_id',UUID(p['resources']['display_file_id']))
    path=ctx.env.root/row['storage_key'];raw=path.read_bytes();path.write_bytes(b'damaged')
    ops=FileOperations(ctx.env.owner,ctx.storage);report=ops.check(mark_missing=True);assert not report['all_available']
    assert ctx.client.get(url).status_code==503
    with pytest.raises(OpsError,match='REPAIR_BYTES_MISMATCH'):ops.repair(row['file_id'],b'wrong')
    ops.repair(row['file_id'],raw);assert ctx.client.get(url).content==raw
    with unit_of_work(ctx.env.owner) as c:assert one(c,'stored_files','file_id',row['file_id'])['sha256']==row['sha256']


def test_maintenance_http_reports_not_ready_and_refuses_login(context):
    ctx=context
    with session_lock(ctx.env.owner,MAINTENANCE_KEY):
        health=ctx.client.get('/api/v1/health').json();assert health['maintenance'] and health['status']=='not_ready'
        assert ctx.client.get('/api/v1/auth/csrf').status_code==503


def test_real_management_cli_dry_run_and_apply(context,tmp_path):
    ctx=context;values=owner_values(ctx.env.db);values['IMPORT_ALLOWED_ROOT']=str(ctx.allowed)
    envfile=private_env(tmp_path/'operations.env',values)
    base=[sys.executable,'-m','backend.app.management','--env-file',str(envfile)]
    try:
        completed=subprocess.run([*base,'import-dry-run','--root',str(ctx.env.root),'--source',str(ctx.source),'--project-id',str(ctx.pid),'--actor-user-id',str(ctx.actor),'--namespace','cli-only/v1','--display-name','CLI 합성 수입'],env=clean_environment(),capture_output=True,text=True,timeout=20)
        assert completed.returncode==0,completed.stdout;preview=json.loads(completed.stdout)['result'];assert preview['state']=='ready'
        applied=subprocess.run([*base,'import-apply','--root',str(ctx.env.root),'--source',str(ctx.source),'--plan-id',preview['operation_id'],'--actor-user-id',str(ctx.actor)],env=clean_environment(),capture_output=True,text=True,timeout=20)
        assert applied.returncode==0,applied.stdout;assert json.loads(applied.stdout)['result']['state']=='completed'
        for password in (values['DB_OWNER_PASSWORD'],ctx.env.db.environment['TEST_DB_ADMIN_PASSWORD']):assert password not in completed.stdout+completed.stderr+applied.stdout+applied.stderr
    finally:envfile.unlink()
