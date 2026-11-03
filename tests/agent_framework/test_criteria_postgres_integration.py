"""Synthetic supplementation/approval in NEW DBs; never the user's real decision."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func,update,insert,delete
from sqlalchemy.exc import DBAPIError

from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database,csrf,email_user,login
from tests.agent_framework.test_pdf_postgres_integration import document_database as initial_pdf_database,runtime,upload
from tests.agent_framework.test_a02_postgres_integration import a02,register,admit,worker
from tests.agent_framework.test_criteria_review import edit
from backend.agent_framework.criteria_contracts import CriteriaVersionRequest,CriteriaApprovalRequest
from backend.agent_framework.criteria_review import versions,bindings
from backend.agent_framework.postgres import runs,steps,jobs,approvals,digest
from backend.agent_framework.postgres_api import create_postgres_app
from backend.agent_framework.postgres_schema import migrate,check_criteria_extension,check_app_permissions
from backend.app.persistence.database import PersistenceError
from backend.app.persistence.schema_inventory import check_schema
from backend.app.persistence.schema.identity import project_members
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError

pytestmark=pytest.mark.db_integration


@pytest.fixture(scope='module')
def document_database(initial_pdf_database):
    auth,migration=initial_pdf_database;db=auth[0]
    before=check_schema(db.engine,db.settings)
    assert migrate(migration,target='AF003')['revision']=='AF003'
    assert migrate(migration,target='AF003')['revision']=='AF003'
    assert migrate(migration,target='AF002')['revision']=='AF003'
    assert check_schema(db.engine,db.settings)==before
    with unit_of_work(db.engine) as c:check_criteria_extension(c);check_app_permissions(c)
    yield auth,migration


@pytest.fixture
def review(a02,request):
    record,raw=register(a02,getattr(request,'param','EXH-NEW004'))
    response=admit(a02,record);assert response.status_code==202,response.text
    rid=response.json()['context']['run_id'];assert worker(a02)[0]=='completed'
    source=a02.client.get('/api/framework/v1/runs/'+rid).json()
    yield SimpleNamespace(**(vars(a02)|{'rid':rid,'route':'/api/framework/v1/runs/'+rid,
        'registered':record,'raw':raw,'draft':source['criteria'],'original':source,
        'review':a02.app.state.criteria_review}))


def post(r,path,body,key=None):
    return r.client.post(r.route+path,json=body,headers={**csrf(r.client),'Idempotency-Key':str(key or uuid4())})


def version_body(r,parent=None,*,resolve=False,purpose='synthetic_test'):
    overview=r.client.get(r.route+'/criteria-review').json()
    edits=[]
    if resolve:
        keep=r.draft['extraction']['unresolved_criterion_ids'][0]
        edits=[edit(c['criterion_id'],action='amend' if c['criterion_id']==keep else 'exclude') for c in r.draft['criteria']]
    return {'parent_version_id':parent['version']['criteria_version_id'] if parent else None,
        'expected_base_sha256':parent['sha256'] if parent else overview['source_draft_sha256'],
        'purpose':purpose,'edits':edits}


def create(r,*,parent=None,resolve=False,purpose='synthetic_test'):
    response=post(r,'/criteria-versions',version_body(r,parent,resolve=resolve,purpose=purpose))
    assert response.status_code==201,response.text
    return response.json()


def approval_body(version,**changes):
    return {'criteria_version_id':version['version']['criteria_version_id'],'expected_sha256':version['sha256'],
        'purpose':'synthetic_test','reason':'격리 DB의 개발용 승인 시뮬레이션. 실제 발주자 결정 아님.',
        'reviewed_all_criteria':True}|changes


def counts(r):
    with unit_of_work(r.db.engine) as c:
        return tuple(c.execute(select(func.count()).select_from(table).where(
            (table.c.source_run_id if table in (versions,bindings) else table.c.run_id)==r.rid)).scalar_one()
            for table in (versions,bindings,approvals))


@pytest.mark.parametrize('review',('EXH-NEW004','CON-NEW043','FES-NEW072'),indirect=True)
def test_revision_approval_and_new_app_reread_keep_original_and_declared_value_sources(review):
    r=review;one=create(r)
    assert one['approval_blockers']
    assert post(r,'/criteria-approvals',approval_body(one)).status_code==409
    two=create(r,parent=one,resolve=True)
    assert not two['approval_blockers'] and two['version']['parent_version_id']==one['version']['criteria_version_id']
    response=post(r,'/criteria-approvals',approval_body(two));assert response.status_code==200,response.text
    approved=response.json();vid=two['version']['criteria_version_id']
    assert approved['criteria']==two and approved['synthetic']
    assert approved['approval']['approved_criteria_version_id']==vid and approved['approval']['criteria_sha256']==two['sha256']
    value=two['version']['criteria'][0]['value_source']
    assert value['purpose']=='synthetic_test' and value['synthetic'] and value['submitted_by']==str(r.subject.user_id)
    assert approved['evidence']['evidence'][0]['value_source']==value
    assert len(two['version']['exclusions'])==len(r.draft['criteria'])-1
    # The fixture intentionally narrows to one development condition; this is not
    # a claim that the real client approved the whole poster scope or values.
    app=create_postgres_app(r.settings,r.config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(app,base_url='http://localhost:8000') as restarted:
        restarted.cookies.update(r.client.cookies)
        assert restarted.get(r.route+'/criteria-approval').json()==approved
        assert restarted.get(r.route+'/criteria-versions').json()==[one,two]
        current=restarted.get(r.route).json()
        assert current['execution_status']=='waiting_asset' and current['approved_criteria_version_id']==vid
        assert current['criteria']==r.draft and current['context']==r.original['context']
        assert current['machine_verdict'] is None and current['human_decision']=='unreviewed'
        assert len(restarted.get(r.route+'/approvals').json())==1
    with unit_of_work(r.db.engine) as c:
        step=c.execute(select(steps).where(steps.c.run_id==r.rid)).mappings().one()
        assert step['output_json']==r.draft and step['output_sha256']==digest(r.draft)
        assert not c.execute(select(jobs).where(jobs.c.run_id==r.rid,jobs.c.phase=='review')).all()
    assert counts(r)==(2,1,1)


def test_idempotent_replays_stale_versions_and_post_approval_edits(review):
    r=review;key=uuid4();body=version_body(r,resolve=True)
    response=post(r,'/criteria-versions',body,key);assert response.status_code==201,response.text
    one=response.json();assert post(r,'/criteria-versions',body,key).json()==one
    assert post(r,'/criteria-versions',body|{'purpose':'human_review'},key).status_code==409
    two=create(r,parent=one)
    assert post(r,'/criteria-approvals',approval_body(one)).status_code==409
    assert post(r,'/criteria-approvals',approval_body(two,expected_sha256='0'*64)).status_code==409
    approval_key=uuid4();request=approval_body(two);approved=post(r,'/criteria-approvals',request,approval_key)
    assert approved.status_code==200,approved.text
    assert post(r,'/criteria-approvals',request,approval_key).json()==approved.json()
    assert post(r,'/criteria-approvals',request|{'reason':'changed'},approval_key).status_code==409
    assert post(r,'/criteria-versions',version_body(r,two)).status_code==409
    assert post(r,'/criteria-versions',body,key).json()==one
    assert counts(r)==(2,1,1)


@pytest.mark.parametrize('same_key',(False,True))
def test_competing_version_requests_are_serialized_without_a_branch(review,same_key):
    r=review;body=CriteriaVersionRequest.model_validate(version_body(r,resolve=True));barrier=Barrier(2);key=uuid4()
    def write(_):
        barrier.wait(timeout=10)
        try:return r.review.create(r.subject,r.rid,key if same_key else uuid4(),body)
        except AuthError as error:
            assert error.code=='CRITERIA_VERSION_CONFLICT';return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(write,range(2)))
    assert counts(r)==(1,0,0)
    assert sum(v is not None for v in results)==(2 if same_key else 1)
    if same_key:assert results[0]==results[1]


def test_concurrent_approval_and_new_revision_cannot_both_succeed(review):
    r=review;one=create(r,resolve=True);barrier=Barrier(2)
    create_request=CriteriaVersionRequest.model_validate(version_body(r,one))
    approve_request=CriteriaApprovalRequest.model_validate(approval_body(one))
    def command(action):
        barrier.wait(timeout=10)
        try:
            if action=='edit':r.review.create(r.subject,r.rid,uuid4(),create_request)
            else:r.review.approve(r.subject,r.rid,uuid4(),approve_request)
            return True
        except AuthError as error:
            assert error.status==409;return False
    with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(command,('edit','approve')))
    assert sum(outcomes)==1
    assert counts(r) in ((2,0,0),(1,1,1))


def test_current_document_version_and_original_bytes_are_rechecked(review):
    r=review;one=create(r,resolve=True)
    reply=upload(r,r.raw,document=r.registered['document_id'],previous=r.registered['document']['document_version_id'])
    assert reply.status_code==201,reply.text
    denied=post(r,'/criteria-approvals',approval_body(one))
    assert denied.status_code==409 and denied.json()['error']['code']=='CRITERIA_DOCUMENT_STALE'
    assert r.client.get(r.route+'/criteria-versions/'+one['version']['criteria_version_id']).status_code==200
    assert counts(r)==(1,0,0)


def test_corrupt_pdf_cannot_be_approved(review):
    from backend.agent_framework.documents import versions as docs
    r=review;one=create(r,resolve=True)
    with unit_of_work(r.db.engine) as c:
        key=c.execute(select(docs.c.storage_key).where(docs.c.document_version_id==UUID(r.registered['document']['document_version_id']))).scalar_one()
    (r.root/key).write_bytes(b'tampered')
    assert post(r,'/criteria-approvals',approval_body(one)).status_code==503
    assert counts(r)==(1,0,0)


def test_approval_is_atomic_and_ack_loss_replays_do_not_duplicate_records(review,monkeypatch):
    r=review;one=create(r,resolve=True);request=approval_body(one);key=uuid4();save=r.review.store._save
    def fail_save(*_):raise PersistenceError('TEST_ROLLBACK','Synthetic failure before approval commit.')
    monkeypatch.setattr(r.review.store,'_save',fail_save)
    assert post(r,'/criteria-approvals',request,key).status_code==503
    assert counts(r)==(1,0,0) and r.client.get(r.route).json()['execution_status']=='waiting_criteria'
    monkeypatch.setattr(r.review.store,'_save',save)
    approve=r.review.approve
    def lose_ack(*args):
        approve(*args)
        raise PersistenceError('TEST_ACK_LOST','Synthetic loss after committed approval.',outcome='commit_unknown')
    monkeypatch.setattr(r.review,'approve',lose_ack)
    assert post(r,'/criteria-approvals',request,key).status_code==503
    assert counts(r)==(1,1,1)
    monkeypatch.setattr(r.review,'approve',approve)
    assert post(r,'/criteria-approvals',request,key).status_code==200
    assert counts(r)==(1,1,1)


def test_version_commit_ack_loss_preserves_first_ids_and_timestamps(review,monkeypatch):
    r=review;create_version=r.review.create;key=uuid4();body=version_body(r)
    def lose_ack(*args):
        create_version(*args)
        raise PersistenceError('TEST_ACK_LOST','Synthetic loss after committed revision.',outcome='commit_unknown')
    monkeypatch.setattr(r.review,'create',lose_ack)
    assert post(r,'/criteria-versions',body,key).status_code==503
    monkeypatch.setattr(r.review,'create',create_version)
    response=post(r,'/criteria-versions',body,key)
    assert response.status_code==201 and counts(r)==(1,0,0)
    assert r.client.get(r.route+'/criteria-versions').json()==[response.json()]


def test_session_csrf_role_and_bounded_json_boundary(review):
    r=review;one=create(r,resolve=True)
    assert r.client.post(r.route+'/criteria-approvals',json=approval_body(one),headers={'Idempotency-Key':str(uuid4())}).status_code==403
    body=version_body(r,one);body['edits']=[edit(one['version']['criteria'][0]['criterion_id'])]
    body['edits'][0]['source']['submitted_by']=str(uuid4())
    assert post(r,'/criteria-versions',body).status_code==422
    assert r.client.post(r.route+'/criteria-versions',content=b'x'*262145,
        headers={**csrf(r.client),'Content-Type':'application/json','Idempotency-Key':str(uuid4())}).status_code==413
    other=email_user(r.auth_database)
    with r.owner.begin() as c:
        c.execute(insert(project_members).values(project_id=r.pid,user_id=UUID(other['id']),role='owner'))
        c.execute(update(project_members).where(project_members.c.project_id==r.pid,project_members.c.user_id==r.subject.user_id).values(role='viewer'))
    assert r.client.get(r.route+'/criteria-versions').status_code==200
    assert post(r,'/criteria-approvals',approval_body(one)).status_code==403
    assert r.client.post('/api/v1/auth/logout',headers=csrf(r.client)).status_code==204
    assert r.client.get(r.route+'/criteria-versions').status_code==401


def test_revision_and_binding_records_are_immutable_for_app_and_owner(review):
    r=review;one=create(r,resolve=True);response=post(r,'/criteria-approvals',approval_body(one))
    assert response.status_code==200,response.text
    for table in (versions,bindings):
        with pytest.raises(PersistenceError):
            with unit_of_work(r.db.engine) as c:c.execute(delete(table).where(table.c.source_run_id==r.rid))
        with pytest.raises(DBAPIError):
            with r.owner.begin() as c:c.execute(update(table).where(table.c.source_run_id==r.rid).values(request_json={}))
        with pytest.raises(DBAPIError):
            with r.owner.begin() as c:c.execute(delete(table).where(table.c.source_run_id==r.rid))
    assert r.client.get(r.route+'/criteria-approval').json()==response.json()


def test_human_declared_purpose_cannot_remove_mock_synthetic_lineage(review):
    r=review;one=create(r,resolve=True,purpose='human_review')
    response=post(r,'/criteria-approvals',approval_body(one,purpose='human_review'))
    assert response.status_code==200,response.text
    result=response.json()
    assert result['synthetic'] and result['criteria']['version']['synthetic'] and result['evidence']['synthetic']
    assert result['criteria']['version']['criteria'][0]['value_source']['synthetic']


def test_versions_from_another_run_cannot_be_read_or_used_as_parent(review):
    r=review;one=create(r,resolve=True)
    response=admit(r,r.registered);assert response.status_code==202,response.text
    second_id=response.json()['context']['run_id'];assert worker(r)[0]=='completed'
    second_route='/api/framework/v1/runs/'+second_id
    vid=one['version']['criteria_version_id']
    assert r.client.get(second_route+'/criteria-versions/'+vid).status_code==404
    body=version_body(r,one)
    response=r.client.post(second_route+'/criteria-versions',json=body,
        headers={**csrf(r.client),'Idempotency-Key':str(uuid4())})
    assert response.status_code==409
    assert r.client.get(second_route+'/criteria-versions').json()==[]
