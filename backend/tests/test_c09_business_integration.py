"""C09 real restricted PostgreSQL role + actual ASGI + private local storage."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import hashlib
import json
from uuid import UUID, uuid4
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select, update, func
from backend.tests.test_v1_auth_integration import auth_database, database, login, csrf, email_user
from backend.app.main import create_app
from backend.app.storage.local import LocalStorage, StorageError, operation_key
from backend.app.persistence.database import PersistenceError
from backend.app.persistence.transaction import unit_of_work
from backend.app.services.business import table
from backend.app.hashing.canonical import digest
from backend.app.contracts.business import draft_payload, selected_items
from backend.app.security.errors import AuthError

pytestmark=pytest.mark.db_integration


@pytest.fixture
def client(auth_database,tmp_path):
    db,_,_,settings,_=auth_database
    root=tmp_path.resolve()/'files';LocalStorage.initialize(root)
    settings=replace(settings,storage_root=root)
    with TestClient(create_app(settings=settings,engine=db.engine),base_url='http://localhost:8000',client=('127.0.0.1',1234)) as current:
        current.headers['Origin']='http://localhost:8000'
        user=email_user(auth_database)
        assert login(current,email=user['email']).status_code==200
        current.test_user=user
        yield current


def post(client,url,body,**headers): return client.post('/api/v1'+url,json=body,headers={**csrf(client),**headers})

def project(client):
    response=post(client,'/projects',{'name':'합성 프로젝트'})
    assert response.status_code==201,response.text
    return response.json()['id']


def upload(client,pid,raw=None):
    if raw is None:
        stream=BytesIO();Image.new('RGBA',(4,3),(255,0,0,0)).save(stream,format='PNG');raw=stream.getvalue()
    return client.post('/api/v1/projects/'+pid+'/images',files={'image':('../../unsafe.jpg',raw,'text/plain')},headers=csrf(client))


def graph(client):
    pid=project(client);response=upload(client,pid)
    assert response.status_code==201,response.text
    image=response.json()
    response=post(client,'/projects/'+pid+'/criteria-sets',{'name':'기준'})
    assert response.status_code==201,response.text
    sid=response.json()['id']
    body=dict(criteria_set_id=sid,image_id=image['id'],raw_requirements='실제 요구사항',items=[
        dict(id='selected',text='행사명 표기',type='information',source='요청 원문',selected=True),
        dict(id='unselected',text='',type='visual',source='초안',selected=False)])
    response=post(client,'/projects/'+pid+'/criteria-drafts',body)
    assert response.status_code==201,response.text
    return pid,image,body,response.json()


def confirm(client,pid,draft,key=None,revision=None):
    return post(client,'/projects/'+pid+'/criteria-versions',dict(draft_id=draft['id'],expected_revision=revision or draft['revision']),
                **{'Idempotency-Key':str(key or uuid4())})


def test_complete_upload_draft_confirmation_and_replay(client,auth_database):
    pid,image,body,draft=graph(client)
    assert image['coordinate_space']=='normalized_original_pixels' and image['actual_format']=='PNG'
    assert 'storage_key' not in str(image)
    response=client.get(image['url']);assert response.status_code==200
    assert hashlib.sha256(response.content).hexdigest()==image['display_sha256']
    assert response.headers['x-content-type-options']=='nosniff'
    with Image.open(BytesIO(response.content)) as display: assert display.getpixel((0,0))==(255,255,255)
    key=uuid4();response=confirm(client,pid,draft,key)
    assert response.status_code==201,response.text
    version=response.json();assert len(version['criteria'])==1 and version['criteria'][0]['is_required'] is None
    replay=confirm(client,pid,draft,key);assert replay.status_code==200 and replay.json()['id']==version['id']
    assert replay.headers['Idempotency-Replayed']=='true'
    with auth_database[1].connect() as connection:
        row=connection.execute(select(table('criteria_versions')).where(table('criteria_versions').c.criteria_version_id==UUID(version['id']))).mappings().one()
        assert row['sealed_at'] is not None and row['confirmed_by']==UUID(client.test_user['id'])
        source=connection.execute(select(table('criteria_drafts')).where(table('criteria_drafts').c.draft_id==UUID(draft['id']))).mappings().one()
        payload=draft_payload(source)
        assert row['source_draft_hash']==digest('criteria_draft',payload)
        assert row['criteria_hash']==digest('criteria_snapshot',dict(payload,items=selected_items(payload)))
        item=connection.execute(select(table('criteria_items')).where(table('criteria_items').c.criteria_version_id==UUID(version['id']))).mappings().one()
        for name in ('rule_code','field_code','expected_value','is_required','source_asset_version_id','source_locator','applicability_jsonb','check_scope_jsonb'):
            assert item[name] is None
        assert item['source_item_jsonb']==body['items'][0]
    assert client.app.state.engine.pool.checkedout()==0


def test_draft_revision_noop_conflict_and_old_version_currentness(client):
    pid,image,body,draft=graph(client);key=uuid4();version=confirm(client,pid,draft,key).json()
    url='/api/v1/projects/'+pid+'/criteria-drafts/'+draft['id']
    values={k:body[k] for k in ('raw_requirements','items')};values.update(expected_revision='1',source_documents=None)
    noop=client.put(url,json=values,headers=csrf(client));assert noop.status_code==200 and noop.json()['updated_at']==draft['updated_at']
    values['items'][1]['text']='미선택 변경'
    changed=client.put(url,json=values,headers=csrf(client));assert changed.status_code==200 and changed.json()['revision']=='2'
    assert client.put(url,json=values,headers=csrf(client)).json()['error']['code']=='DRAFT_REVISION_CONFLICT'
    replay=confirm(client,pid,draft,key);assert replay.status_code==200 and not replay.json()['draft_is_current']
    assert replay.json()['criteria_hash']==version['criteria_hash']
    assert confirm(client,pid,draft,revision='1').json()['error']['code']=='DRAFT_REVISION_CONFLICT'
    assert confirm(client,pid,draft,key,revision='2').json()['error']['code']=='IDEMPOTENCY_KEY_REUSED'
    archived=client.patch('/api/v1/projects/'+pid,json={'archived':True},headers=csrf(client));assert archived.status_code==200
    assert confirm(client,pid,draft,key).status_code==200
    assert confirm(client,pid,draft,revision='2').json()['error']['code']=='PROJECT_ARCHIVED'
    assert client.patch('/api/v1/projects/'+pid,json={'archived':False},headers=csrf(client)).status_code==200


@pytest.mark.parametrize('invalid', ['empty','blank'])
def test_selection_validation_preserves_draft(client,invalid):
    pid,image,body,draft=graph(client)
    values=dict(expected_revision='1',raw_requirements=body['raw_requirements'],items=body['items'],source_documents=None)
    if invalid=='empty': values['items'][0]['selected']=False
    else: values['items'][0]['text']=' '
    response=client.put('/api/v1/projects/'+pid+'/criteria-drafts/'+draft['id'],json=values,headers=csrf(client));assert response.status_code==200
    assert confirm(client,pid,response.json()).status_code==422


@pytest.mark.parametrize('resource', ['image','set','source','draft','version','filter'])
def test_foreign_project_context_is_rejected(client,resource):
    pid,image,body,draft=graph(client);other=project(client)
    if resource in ('image','set'):
        foreign=dict(body)
        if resource=='image': foreign['criteria_set_id']=post(client,'/projects/'+other+'/criteria-sets',{'name':'other'}).json()['id']
        else: foreign['image_id']=upload(client,other).json()['id']
        response=post(client,'/projects/'+other+'/criteria-drafts',foreign)
    elif resource=='source':
        foreign=dict(body,source_documents=[{'asset_version_id':upload(client,other).json()['id'],'locator':None}])
        response=post(client,'/projects/'+pid+'/criteria-drafts',foreign)
    elif resource=='draft': response=client.get('/api/v1/projects/'+other+'/criteria-drafts/'+draft['id'])
    elif resource=='filter': response=client.get('/api/v1/projects/'+other+'/criteria-drafts?image_id='+image['id'])
    else:
        version=confirm(client,pid,draft).json()
        response=client.get('/api/v1/projects/'+other+'/criteria-versions/'+version['id'])
    assert response.status_code==404,response.text


@pytest.mark.parametrize('query', ['limit=0','limit=101','limit=true','limit=1&limit=2','unknown=x','archived=bad','cursor=forged'])
def test_strict_lists(client,query):
    assert client.get('/api/v1/projects?'+query).status_code in (400,422)


def test_cursor_keyset_context_and_archive_filters(client):
    ids=[project(client) for _ in range(3)]
    first=client.get('/api/v1/projects?limit=2').json();assert first['has_more']
    next_page=client.get('/api/v1/projects',params={'limit':2,'cursor':first['next_cursor']}).json()
    assert len({x['id'] for x in first['items']+next_page['items']})==3
    assert client.get('/api/v1/projects',params={'limit':3,'cursor':first['next_cursor']}).status_code==400
    assert client.patch('/api/v1/projects/'+ids[0],json={'archived':True},headers=csrf(client)).status_code==200
    assert len(client.get('/api/v1/projects?archived=only').json()['items'])==1
    assert len(client.get('/api/v1/projects?archived=include').json()['items'])==3


@pytest.mark.parametrize('kind',['missing','tamper','hardlink','symlink'])
def test_content_integrity_error_preserves_database_metadata(client,auth_database,kind):
    pid=project(client);image=upload(client,pid).json()
    storage=client.app.state.storage
    with auth_database[1].connect() as connection:
        row=connection.execute(select(table('stored_files')).where(table('stored_files').c.file_role=='display_image',table('stored_files').c.project_id==UUID(pid))).mappings().one()
        before=dict(row)
    path=storage.path/row['storage_key']
    if kind=='tamper': path.write_bytes(b'corrupt')
    elif kind=='hardlink':
        import os
        os.link(path,storage.path/'staging'/'unexpected')
    else:
        path.unlink()
        if kind=='symlink': path.symlink_to(storage.path/'marker')
    response=client.get(image['url']);assert response.status_code==503 and response.json()['error']['code']=='FILE_UNAVAILABLE'
    with auth_database[1].connect() as connection:
        after=dict(connection.execute(select(table('stored_files')).where(table('stored_files').c.file_id==row['file_id'])).mappings().one())
    assert before==after


@pytest.mark.parametrize('kind',['text','gif','animation','overbytes','overpixels','doublefile','extra_field'])
def test_upload_actual_input_rejections(client,kind):
    pid=project(client)
    if kind=='text': raw=b'bad'
    elif kind=='overbytes': raw=b'x'*(20*1024*1024+1)
    elif kind=='doublefile':
        response=client.post('/api/v1/projects/'+pid+'/images',files=[('image',('a',b'x')),('image',('b',b'y'))],headers=csrf(client))
        assert response.status_code==422;return
    elif kind=='extra_field':
        response=client.post('/api/v1/projects/'+pid+'/images',files={'image':('a',b'x')},data={'name':'unwanted'},headers=csrf(client))
        assert response.status_code==422;return
    else:
        out=BytesIO();image=Image.new('RGB',(4000,3000) if kind=='overpixels' else (2,2),'red')
        image.save(out,format='GIF' if kind=='gif' else 'PNG',**({'save_all':True,'append_images':[Image.new('RGB',(2,2),'blue')]} if kind=='animation' else {}));raw=out.getvalue()
    response=upload(client,pid,raw)
    assert response.status_code in (413,422),response.text
    assert client.get('/api/v1/projects/'+pid+'/images').json()['items']==[]


def test_same_bytes_are_distinct_assets_and_original_preserved(client,auth_database):
    pid=project(client);first=upload(client,pid).json();second=upload(client,pid).json()
    assert first['id']!=second['id'] and first['asset_id']!=second['asset_id']
    assert first['original_sha256']==second['original_sha256']
    with auth_database[1].connect() as connection:
        files=connection.execute(select(table('stored_files')).where(table('stored_files').c.project_id==UUID(pid))).mappings().all()
        assert len(files)==4
        for file in files:
            with client.app.state.storage.verified(file['storage_key'],file['byte_size'],file['sha256']) as stream: assert stream.read()


def test_concurrent_same_key_replays_one_sealed_graph(client,auth_database):
    pid,image,body,draft=graph(client);who=client.app.state.auth.current(client.cookies['baro_session'],developer_allowed=True)
    from backend.app.contracts.business import Confirm
    data=Confirm(draft_id=draft['id'],expected_revision='1');key=uuid4()
    def work(_): return client.app.state.business.confirm(who,True,UUID(pid),data,key,str(uuid4()))
    with ThreadPoolExecutor(max_workers=2) as workers: results=list(workers.map(work,range(2)))
    assert len({result[0]['id'] for result in results})==1 and sorted(result[1] for result in results)==[False,True]
    with auth_database[1].connect() as connection:
        assert connection.execute(select(func.count()).select_from(table('criteria_versions')).where(table('criteria_versions').c.project_id==UUID(pid))).scalar_one()==1


def test_concurrent_distinct_keys_project_numbers_and_equal_content_hash(client):
    pid,image,body,draft=graph(client);who=client.app.state.auth.current(client.cookies['baro_session'],developer_allowed=True)
    from backend.app.contracts.business import Confirm
    data=Confirm(draft_id=draft['id'],expected_revision='1')
    def work(_): return client.app.state.business.confirm(who,True,UUID(pid),data,uuid4(),str(uuid4()))[0]
    with ThreadPoolExecutor(max_workers=2) as workers: results=list(workers.map(work,range(2)))
    assert sorted(result['version_no'] for result in results)==[1,2]
    assert len({result['criteria_hash'] for result in results})==1


@pytest.mark.parametrize('change',['archive','revoke_member','disable_user'])
def test_upload_rechecks_privileges_after_file_work(client,auth_database,monkeypatch,change):
    pid=project(client);files=client.app.state.files;original=files.persist
    def changed(*args):
        if change=='archive':
            with auth_database[1].begin() as connection: connection.execute(update(table('projects')).where(table('projects').c.project_id==UUID(pid)).values(business_state='archived',archived_at=func.clock_timestamp(),updated_at=func.clock_timestamp()))
        elif change=='disable_user': auth_database[2].set_user_active(UUID(client.test_user['id']),False)
        else:
            # Add another owner before revoking the current last owner.
            other=email_user(auth_database)
            auth_database[2].set_project_member(UUID(pid),UUID(other['id']),'owner',True)
            auth_database[2].set_project_member(UUID(pid),UUID(client.test_user['id']),'viewer',False)
        return original(*args)
    monkeypatch.setattr(files,'persist',changed)
    response=upload(client,pid);assert response.status_code in (401,404,409),response.text
    with auth_database[1].connect() as connection:
        assert connection.execute(select(func.count()).select_from(table('asset_versions')).where(table('asset_versions').c.project_id==UUID(pid))).scalar_one()==0
    journal=list(client.app.state.storage.journals())[-1];assert journal['db_outcome']=='rolled_back'
    for item in journal['files']:
        with client.app.state.storage.verified(item['storage_key'],int(item['byte_size']),item['sha256']): pass


@pytest.mark.parametrize('kind',['committed','not_committed','unresolved'])
def test_commit_acknowledgement_loss_uses_fresh_reconciliation(client,monkeypatch,kind):
    pid=project(client);files=client.app.state.files;original=files.persist
    def lost(*args):
        if kind=='committed': original(*args)
        raise PersistenceError('DB_COMMIT_OUTCOME_UNKNOWN','synthetic',outcome='commit_unknown')
    monkeypatch.setattr(files,'persist',lost)
    if kind=='unresolved': monkeypatch.setattr(files,'resolve',lambda intent:'unresolved')
    response=upload(client,pid)
    assert response.status_code==(201 if kind=='committed' else 503),response.text
    journal=list(client.app.state.storage.journals())[-1];assert journal['db_outcome']==kind
    for item in journal['files']:
        with client.app.state.storage.verified(item['storage_key'],int(item['byte_size']),item['sha256']): pass


def test_restart_reconciles_real_committed_graph_and_checks_service_lock(client,auth_database):
    pid=project(client);image=upload(client,pid).json();app=client.app
    with pytest.raises(Exception):
        with TestClient(create_app(settings=app.state.settings,engine=auth_database[0].engine)): pass
    journals=list(app.state.storage.journals());journal=journals[-1];journal['db_outcome']='commit_unknown';app.state.storage.journal(journal)
    assert app.state.files.resolve(journal)=='committed'
    app.state.storage.close()
    with TestClient(create_app(settings=app.state.settings,engine=auth_database[0].engine),base_url='http://localhost:8000',client=('127.0.0.1',99)) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get(image['url']).status_code==200
        assert restarted.app.state.recovery[journal['operation_id']]=='committed'


def test_operation_db_lock_prevents_false_absence_and_conflicts_are_preserved(client,auth_database):
    pid=project(client);assert upload(client,pid).status_code==201
    intent=list(client.app.state.storage.journals())[-1]
    with unit_of_work(auth_database[0].engine) as connection:
        connection.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)',(operation_key(intent['operation_id']),))
        assert client.app.state.files.resolve(intent)=='unresolved'
    changed=json.loads(json.dumps(intent));changed['files'][0]['sha256']='0'*64
    assert client.app.state.files.resolve(changed)=='conflict'
    assert client.app.state.files.resolve(intent)=='committed'


@pytest.mark.parametrize('role',['viewer','editor'])
def test_current_project_role_controls_every_mutation_and_replay(client,auth_database,role):
    pid,image,body,draft=graph(client);key=uuid4();assert confirm(client,pid,draft,key).status_code==201
    other=email_user(auth_database);auth_database[2].set_project_member(UUID(pid),UUID(other['id']),'owner',True)
    auth_database[2].set_project_member(UUID(pid),UUID(client.test_user['id']),role,True)
    assert client.get('/api/v1/projects/'+pid).status_code==200
    assert client.patch('/api/v1/projects/'+pid,json={'name':'forbidden'},headers=csrf(client)).status_code==403
    assert upload(client,pid).status_code==(403 if role=='viewer' else 201)
    assert confirm(client,pid,draft,key).status_code==(403 if role=='viewer' else 200)


def test_global_actor_key_conflicts_across_projects_but_other_actor_is_independent(client,auth_database):
    pid,image,body,draft=graph(client);key=uuid4();assert confirm(client,pid,draft,key).status_code==201
    second,_,_,other_draft=graph(client)
    response=confirm(client,second,other_draft,key);assert response.status_code==409
    assert 'criteria_hash' not in response.text
    other=email_user(auth_database);auth_database[2].set_project_member(UUID(pid),UUID(other['id']),'editor',True)
    assert login(client,email=other['email']).status_code==200
    response=confirm(client,pid,draft,key);assert response.status_code==201,response.text
    assert response.json()['version_no']==2


def test_bigint_revision_max_roundtrips_and_blocks_increment(client,auth_database):
    pid,image,body,draft=graph(client);new_id=uuid4()
    with auth_database[1].begin() as connection:
        from sqlalchemy import insert
        connection.execute(insert(table('criteria_drafts')).values(draft_id=new_id,project_id=UUID(pid),criteria_set_id=UUID(body['criteria_set_id']),
            target_asset_version_id=UUID(image['id']),revision=9223372036854775807,raw_requirements='max',items_jsonb=body['items'],updated_by=UUID(client.test_user['id'])))
    url='/api/v1/projects/'+pid+'/criteria-drafts/'+str(new_id)
    current=client.get(url);assert current.json()['revision']=='9223372036854775807'
    values=dict(expected_revision='9223372036854775807',raw_requirements='changed',items=body['items'],source_documents=None)
    response=client.put(url,json=values,headers=csrf(client));assert response.status_code==409
    assert response.json()['error']['code']=='REVISION_LIMIT_REACHED'
    values['raw_requirements']='max'
    assert client.put(url,json=values,headers=csrf(client)).status_code==200


def test_database_failure_mid_graph_rolls_back_and_keeps_published_files(client,auth_database):
    from sqlalchemy import event
    pid=project(client);engine=auth_database[0].engine;fired=[]
    def fail(connection,cursor,statement,parameters,context,executemany):
        if 'INSERT INTO public.assets ' in statement and not fired:
            fired.append(True);cursor.execute('SELECT 1/0')
    event.listen(engine,'before_cursor_execute',fail)
    try: response=upload(client,pid)
    finally: event.remove(engine,'before_cursor_execute',fail)
    assert fired and response.status_code==503,response.text
    with auth_database[1].connect() as connection:
        for name in ('stored_files','assets','asset_versions','image_coordinate_frames'):
            assert connection.execute(select(func.count()).select_from(table(name)).where(table(name).c.project_id==UUID(pid))).scalar_one()==0
    intent=list(client.app.state.storage.journals())[-1]
    assert intent['db_outcome']=='not_committed'
    for item in intent['files']:
        with client.app.state.storage.verified(item['storage_key'],int(item['byte_size']),item['sha256']): pass


def test_hash_decode_and_filesystem_work_hold_no_database_connection(client,auth_database,monkeypatch):
    import backend.app.services.files as files_module
    import backend.app.services.business as business_module
    engine=auth_database[0].engine;normalizer=files_module.normalize;hasher=business_module.digest;publish=client.app.state.storage.publish
    seen=[]
    def normalized(*args):
        assert engine.pool.checkedout()==0;seen.append('decode');return normalizer(*args)
    def hashed(*args,**kwargs):
        assert engine.pool.checkedout()==0;seen.append('hash');return hasher(*args,**kwargs)
    def published(*args):
        assert engine.pool.checkedout()==0;seen.append('publish');return publish(*args)
    monkeypatch.setattr(files_module,'normalize',normalized);monkeypatch.setattr(business_module,'digest',hashed)
    monkeypatch.setattr(client.app.state.storage,'publish',published)
    pid,image,body,draft=graph(client);assert confirm(client,pid,draft).status_code==201
    assert seen.count('hash')==3 and seen.count('publish')==2 and seen.count('decode')==1


@pytest.mark.parametrize('phase',['first_payload','before_db'])
def test_abrupt_process_exit_leaves_journal_and_bytes_recoverable(client,auth_database,tmp_path,phase):
    import subprocess,sys
    from backend.app.storage.local import key
    pid=project(client);root=tmp_path.resolve()/'crashed-storage';LocalStorage.initialize(root)
    data=b'crash-test';op=str(uuid4())
    intent=dict(contract_version='cc-storage-journal-v1',operation='upload_image',operation_id=op,project_id=pid,
        asset_id=str(uuid4()),image_id=str(uuid4()),actor_id=client.test_user['id'],original_frame_id=str(uuid4()),display_frame_id=str(uuid4()),
        measurements={},phase='staged',db_outcome='unconfirmed',files=[])
    for role in ('original_image','display_image'):
        fid=str(uuid4());intent['files'].append(dict(project_id=pid,file_id=fid,file_role=role,storage_key=key(pid,fid),byte_size=str(len(data)),sha256=hashlib.sha256(data).hexdigest()))
    intent_path=tmp_path/'synthetic-intent.json';intent_path.write_text(json.dumps(intent))
    script='''import json,os,sys
from backend.app.storage.local import LocalStorage
s=LocalStorage(sys.argv[1]);s.start();v=json.load(open(sys.argv[2]));s.journal(v)
with s.lock('operation-'+v['operation_id']),s.lock('gate',shared=True):
 for item in v['files']: s.write_new('staging/'+v['operation_id']+'/'+item['file_id'],b'crash-test')
 for n,item in enumerate(v['files']):
  s.publish(v['operation_id'],item)
  if sys.argv[3]=='first_payload' and n==0: os._exit(23)
 os._exit(23)
'''
    result=subprocess.run([sys.executable,'-c',script,str(root),str(intent_path),phase],capture_output=True,timeout=15)
    assert result.returncode==23
    settings=replace(client.app.state.settings,storage_root=root)
    with TestClient(create_app(settings=settings,engine=auth_database[0].engine),base_url='http://localhost:8000',client=('127.0.0.1',98)) as recovered:
        assert recovered.app.state.recovery[op]=='not_committed'
        first=intent['files'][0]
        with recovered.app.state.storage.verified(first['storage_key'],len(data),first['sha256']) as stream: assert stream.read()==data
        assert list(recovered.app.state.storage.journals())[0]['db_outcome']=='not_committed'


@pytest.mark.parametrize('body,status',[(b'{"name":"a","name":"b"}',400),(b'{"name":"\\ud800"}',400),
    (b'{"name":NaN}',400),(b' '*262145,413),(b'{"name":1}',422),(b'{"name":"x","role":"owner"}',422),
    (b'['*34+b'0'+b']'*34,400)])
def test_business_json_errors_are_bounded_and_safe(client,body,status):
    response=client.post('/api/v1/projects',content=body,headers={**csrf(client),'Content-Type':'application/json'})
    assert response.status_code==status,response.text
    assert response.json()['error']['fields']=={}


def test_streamed_multipart_total_limit_ignores_fake_content_length(client):
    pid=project(client)
    boundary='bounded-synthetic-boundary'
    def chunks():
        yield ('--'+boundary+'\r\nContent-Disposition: form-data; name="image"; filename="a.png"\r\n\r\n').encode()
        for _ in range(22): yield b'x'*1024*1024
        yield ('\r\n--'+boundary+'--\r\n').encode()
    response=client.post('/api/v1/projects/'+pid+'/images',content=chunks(),headers={**csrf(client),'Content-Type':'multipart/form-data; boundary='+boundary,'Content-Length':'1'})
    assert response.status_code==413,response.text
