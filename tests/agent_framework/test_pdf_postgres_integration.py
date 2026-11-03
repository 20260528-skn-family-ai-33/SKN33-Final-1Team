"""PDF registration against a disposable real PostgreSQL 17 DB, never live data."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID,uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func,insert,select,update

from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database,email_user,login,csrf
from backend.migrations.configuration import load_migration_settings
from backend.app.contracts.business import ProjectCreate
from backend.app.persistence.database import PersistenceError
from backend.app.persistence.schema.identity import project_members,projects
from backend.app.persistence.schema_inventory import check_schema
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from backend.app.services.business import Business
from backend.app.storage.local import LocalStorage,StorageError
from backend.agent_framework.adapters.project_files import LegacyStorageReader
from backend.agent_framework.adapters.synthetic import sample_inputs
from backend.agent_framework.composition import load_config
from backend.agent_framework.contracts import DocumentRef
from backend.agent_framework.documents import (
    RegisteredProjectCatalog,initialize_document_storage,versions,
)
from backend.agent_framework.files import VerifiedFileAccess
from backend.agent_framework.postgres_api import create_postgres_app
from backend.agent_framework.postgres_schema import migrate,check_extension,check_app_permissions

pytestmark=pytest.mark.db_integration
ROOT=Path(__file__).resolve().parents[2]
PDFS=ROOT/'docs/poster-baselines/2026-11-03/workflow/client-inputs'


def pdf(text='Synthetic PDF version one'):
    with pymupdf.open() as doc:
        doc.new_page().insert_text((30,40),text)
        return doc.tobytes()


@pytest.fixture(scope='module')
def document_database(auth_database):
    db,owner,_manager,settings,_setup=auth_database
    env=db.environment
    migration=load_migration_settings({'DB_HOST':env['TEST_DB_HOST'],'DB_PORT':env['TEST_DB_PORT'],
        'POSTGRES_DB':db.name,'DB_OWNER_USER':env['TEST_DB_OWNER_USER'],
        'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'],'DB_APP_USER':env['TEST_DB_APP_USER']})
    before=check_schema(db.engine,db.settings)
    assert migrate(migration)['revision']=='AF001'
    assert migrate(migration,target='AF002')['revision']=='AF002'
    assert migrate(migration,target='AF002')['revision']=='AF002'
    assert migrate(migration)['revision']=='AF002'  # no implicit downgrade
    assert check_schema(db.engine,db.settings)==before
    with unit_of_work(db.engine) as c:
        assert check_extension(c)['revision']=='AF002'
        check_app_permissions(c)
    yield auth_database,migration


@pytest.fixture
def runtime(document_database,tmp_path):
    auth_database,migration=document_database
    db,owner,_manager,settings,_setup=auth_database
    root=tmp_path.resolve()/'pdf-storage'
    initialize_document_storage(root)
    config=load_config('examples/agent_framework/team_config.synthetic.json')
    app=create_postgres_app(settings,config,engine=db.engine,pdf_storage_root=root)
    user=email_user(auth_database)
    with TestClient(app,base_url='http://localhost:8000',client=('127.0.0.1',45)) as client:
        client.headers['Origin']='http://localhost:8000'
        assert login(client,email=user['email']).status_code==200
        subject=app.state.auth.current(client.cookies[settings.session_cookie])
        business=Business(db.engine,app.state.authorization,settings)
        project=business.create_project(subject,False,ProjectCreate(name='Creative poster PDF test'),str(uuid4()))
        yield SimpleNamespace(client=client,app=app,service=app.state.documents,subject=subject,
            pid=UUID(project['id']),base='/api/framework/v1/projects/'+project['id'],db=db,owner=owner,
            root=root,settings=settings,config=config,auth_database=auth_database,migration=migration)


def upload(r,raw=None,*,key=None,filename='기준 문서.pdf',document=None,previous=None,headers=None):
    route=r.base+'/documents'
    params={'filename':filename}
    if document:
        route+='/'+document+'/versions';params['previous_version_id']=previous
    return r.client.post(route,params=params,content=raw if raw is not None else pdf(),
        headers=headers if headers is not None else {**csrf(r.client),'Content-Type':'application/pdf','Idempotency-Key':str(key or uuid4())})


def row_count(r):
    with unit_of_work(r.db.engine) as c:
        return c.execute(select(func.count()).select_from(versions).where(versions.c.project_id==r.pid)).scalar_one()


@pytest.mark.parametrize('case',('EXH-NEW004','CON-NEW043','FES-NEW072'))
def test_real_sample_pdf_registration_download_and_worker_catalog(runtime,case):
    r=runtime;path=next((PDFS/case).glob('*.pdf'));raw=path.read_bytes()
    reply=upload(r,raw,filename=path.name)
    assert reply.status_code==201,reply.text
    record=reply.json();ref=record['document'];vid=ref['document_version_id']
    assert record['version_no']==1 and record['previous_version_id'] is None
    assert ref['sha256']==sha256(raw).hexdigest() and ref['page_count']==2
    assert record['byte_size']==len(raw) and record['original_name']==path.name
    assert r.client.get(r.base+'/document-versions/'+vid).json()==record
    content=r.client.get(r.base+'/document-versions/'+vid+'/content')
    assert content.content==raw and content.headers['content-type']=='application/pdf'
    assert "filename*=UTF-8''" in content.headers['content-disposition']
    assert 'storage_key' not in reply.text and str(r.root) not in reply.text
    context,_,_=sample_inputs()
    context=context.model_copy(update={'actor_id':str(r.subject.user_id),'project_id':str(r.pid),'document_version_id':vid})
    access=VerifiedFileAccess(RegisteredProjectCatalog(r.service,r.subject),LegacyStorageReader(r.service.storage))
    opened=access.read_document(context,DocumentRef.model_validate(ref))
    assert opened.content==raw
    assert r.client.get('/api/framework/v1/health').json()['schema']=='AF002'


def test_idempotency_version_history_and_new_app_readback(runtime):
    r=runtime;raw=pdf();request_key=uuid4()
    first=upload(r,raw,key=request_key);assert first.status_code==201,first.text
    one=first.json();doc=one['document_id'];vid=one['document']['document_version_id']
    assert upload(r,raw,key=request_key).json()==one
    assert upload(r,pdf('changed'),key=request_key).status_code==409
    assert upload(r,raw,key=request_key,filename='다른 이름.pdf').status_code==409
    new_raw=pdf('Second immutable PDF')
    second=upload(r,new_raw,document=doc,previous=vid);assert second.status_code==201,second.text
    two=second.json();assert two['version_no']==2 and two['previous_version_id']==vid
    assert upload(r,raw,document=doc,previous=vid).status_code==409
    assert row_count(r)==2
    app=create_postgres_app(r.settings,r.config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(app,base_url='http://localhost:8000') as restarted:
        restarted.cookies.update(r.client.cookies)
        assert restarted.get(r.base+'/documents/'+doc+'/versions').json()==[one,two]
        assert restarted.get(r.base+'/document-versions/'+vid+'/content').content==raw
        assert restarted.get(r.base+'/document-versions/'+two['document']['document_version_id']+'/content').content==new_raw


@pytest.mark.parametrize('kind,status',(('format',422),('name',422),('media',415),('key',422),('csrf',403),('too_large',413),('encrypted',422),('pages',413)))
def test_invalid_uploads_do_not_create_payloads_or_rows(runtime,kind,status):
    r=runtime;raw=pdf();name='기준.pdf'
    headers={**csrf(r.client),'Content-Type':'application/pdf','Idempotency-Key':str(uuid4())}
    if kind=='format':raw=b'%PDF-broken'
    if kind=='name':name='../escape.pdf'
    if kind=='media':headers['Content-Type']='image/png'
    if kind=='key':headers['Idempotency-Key']='not-a-uuid'
    if kind=='csrf':headers.pop('X-CSRFToken')
    if kind=='too_large':raw=b'%PDF-'+b'a'*(20*1024*1024)
    if kind in ('encrypted','pages'):
        with pymupdf.open() as doc:
            for _ in range(51 if kind=='pages' else 1):doc.new_page()
            raw=doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='user') if kind=='encrypted' else doc.tobytes()
    reply=upload(r,raw,filename=name,headers=headers)
    assert reply.status_code==status,reply.text
    assert row_count(r)==0 and not list((r.root/'objects').rglob('payload.bin'))


def test_project_membership_session_and_archive_boundary(runtime):
    r=runtime;record=upload(r).json();vid=record['document']['document_version_id']
    assert r.client.get('/api/framework/v1/projects/'+str(uuid4())+'/document-versions/'+vid).status_code==404
    second_owner=email_user(r.auth_database)
    with r.owner.begin() as c:
        c.execute(insert(project_members).values(project_id=r.pid,user_id=UUID(second_owner['id']),role='owner'))
        c.execute(update(project_members).where(project_members.c.project_id==r.pid,
            project_members.c.user_id==r.subject.user_id).values(role='viewer'))
    assert upload(r).status_code==403
    assert r.client.get(r.base+'/document-versions/'+vid+'/content').status_code==200
    with r.owner.begin() as c:
        c.execute(update(project_members).where(project_members.c.project_id==r.pid,
            project_members.c.user_id==r.subject.user_id).values(role='owner'))
        c.execute(update(projects).where(projects.c.project_id==r.pid).values(business_state='archived',archived_at=func.clock_timestamp()))
    assert upload(r).status_code==409
    assert r.client.get(r.base+'/document-versions/'+vid).status_code==200
    assert r.client.post('/api/v1/auth/logout',headers=csrf(r.client)).status_code==204
    assert upload(r).status_code==401
    assert r.client.get(r.base+'/document-versions/'+vid).status_code==401
    assert row_count(r)==1


def test_files_are_verified_and_never_overwritten_on_retry(runtime):
    r=runtime;raw=pdf();request_key=uuid4();reply=upload(r,raw,key=request_key).json()
    with unit_of_work(r.db.engine) as c:
        row=c.execute(select(versions).where(versions.c.project_id==r.pid)).mappings().one()
    path=r.root/row['storage_key'];path.write_bytes(b'tampered')
    vid=reply['document']['document_version_id']
    assert r.client.get(r.base+'/document-versions/'+vid+'/content').status_code==503
    assert upload(r,raw,key=request_key).status_code==503
    assert path.read_bytes()==b'tampered' and row_count(r)==1


def test_commit_ack_loss_is_reconciled_without_duplicate_write(runtime,monkeypatch):
    r=runtime;persist=r.service._persist;calls=[]
    def lose_ack(*args):
        calls.append(1);persist(*args)
        raise PersistenceError('TEST_ACK_LOST','Synthetic acknowledgement loss.',outcome='commit_unknown')
    monkeypatch.setattr(r.service,'_persist',lose_ack)
    reply=upload(r)
    assert reply.status_code==201,reply.text
    assert len(calls)==1 and row_count(r)==1


def test_unconfirmed_transaction_is_not_automatically_retried(runtime,monkeypatch):
    r=runtime;persist=r.service._persist;calls=[];raw=pdf();request_key=uuid4()
    def unavailable(*args):
        calls.append(1)
        raise PersistenceError('TEST_DB_UNAVAILABLE','Synthetic connection loss.',outcome='commit_unknown')
    monkeypatch.setattr(r.service,'_persist',unavailable)
    reply=upload(r,raw,key=request_key)
    assert reply.status_code==503 and reply.json()['error']['code']=='DOCUMENT_UPLOAD_OUTCOME_UNCONFIRMED'
    assert len(calls)==1 and row_count(r)==0
    paths=list((r.root/'objects').rglob('payload.bin'));assert len(paths)==1
    monkeypatch.setattr(r.service,'_persist',persist)
    assert upload(r,raw,key=request_key).status_code==201
    assert list((r.root/'objects').rglob('payload.bin'))==paths and row_count(r)==1


def test_two_requests_with_same_key_have_one_durable_version(runtime):
    r=runtime;raw=pdf();request_key=uuid4();barrier=Barrier(2)
    def register(_):
        barrier.wait(timeout=10)
        try:return r.service.register(r.subject,False,r.pid,request_key,raw)
        except AuthError as error:
            assert error.status==503
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(register,range(2)))
    final=r.service.register(r.subject,False,r.pid,request_key,raw)
    assert all(value is None or value==final for value in results) and row_count(r)==1


def test_metadata_is_insert_only_for_application_and_sealed_for_owner(runtime):
    r=runtime;record=upload(r).json();vid=UUID(record['document']['document_version_id'])
    with pytest.raises(PersistenceError):
        with unit_of_work(r.db.engine) as c:
            c.execute(update(versions).where(versions.c.document_version_id==vid).values(original_name='changed.pdf'))
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError):
        with r.owner.begin() as c:c.execute(update(versions).where(versions.c.document_version_id==vid).values(original_name='changed.pdf'))
    assert r.client.get(r.base+'/document-versions/'+str(vid)).json()==record


def test_competing_new_versions_cannot_fork_one_document(runtime):
    r=runtime;first=upload(r).json();barrier=Barrier(2)
    doc=UUID(first['document_id']);prev=UUID(first['document']['document_version_id'])
    def register(index):
        raw=pdf('Competing version '+str(index));barrier.wait(timeout=10)
        try:return r.service.register(r.subject,False,r.pid,uuid4(),raw,document_id=doc,previous_version_id=prev)
        except AuthError as error:
            assert error.status==409 and error.code=='DOCUMENT_VERSION_CONFLICT'
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(register,range(2)))
    assert sum(value is not None for value in results)==1 and row_count(r)==2


def test_publication_crash_reuses_original_intent_and_payload(runtime,monkeypatch):
    r=runtime;publish=r.service.storage.publish;raw=pdf();request_key=uuid4()
    def crash(*args):
        publish(*args)
        raise OSError('Synthetic failure after publication.')
    monkeypatch.setattr(r.service.storage,'publish',crash)
    assert upload(r,raw,key=request_key).status_code==503 and row_count(r)==0
    paths=list((r.root/'objects').rglob('payload.bin'));assert len(paths)==1
    monkeypatch.setattr(r.service.storage,'publish',publish)
    assert upload(r,raw,key=request_key).status_code==201
    assert list((r.root/'objects').rglob('payload.bin'))==paths and row_count(r)==1


def test_membership_revocation_during_publication_prevents_db_insert(runtime,monkeypatch):
    r=runtime;publish=r.service._publish
    def revoke(*args):
        publish(*args)
        with r.owner.begin() as c:
            c.execute(update(project_members).where(project_members.c.project_id==r.pid,
                project_members.c.user_id==r.subject.user_id).values(is_active=False))
    # Another owner preserves the existing M008 owner invariant.
    other=email_user(r.auth_database)
    with r.owner.begin() as c:c.execute(insert(project_members).values(project_id=r.pid,user_id=UUID(other['id']),role='owner'))
    monkeypatch.setattr(r.service,'_publish',revoke)
    assert upload(r).status_code==404 and row_count(r)==0


def test_storage_root_must_be_new_and_pdf_specific(tmp_path):
    from backend.agent_framework.documents import open_document_storage
    root=tmp_path.resolve()/'legacy';LocalStorage.initialize(root)
    with pytest.raises(FileExistsError):initialize_document_storage(root)
    with pytest.raises(FileNotFoundError):open_document_storage(root)
