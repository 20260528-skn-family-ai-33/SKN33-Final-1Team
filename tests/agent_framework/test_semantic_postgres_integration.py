"""Isolated PostgreSQL, fake model outputs and scripted test approvals only."""
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func

from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database,csrf,email_user,login
from tests.agent_framework.test_pdf_postgres_integration import document_database as initial_database,runtime,upload,PDFS
from tests.agent_framework.test_a02_semantic import BRIEF,model_reply,requirement
from backend.agent_framework.a02 import QwenProvider,build_postgres_agents
from backend.agent_framework.a02_configuration import PDFExtractionConfiguration
from backend.agent_framework.composition import load_config
from backend.agent_framework.documents import versions,open_document_storage
from backend.agent_framework.durable_worker import PostgresQueue,execute_claim
from backend.agent_framework.postgres import steps,digest
from backend.agent_framework.postgres_api import create_postgres_app
from backend.agent_framework.postgres_schema import migrate,check_app_permissions,check_document_extension,check_criteria_extension
from backend.app.persistence.schema_inventory import check_schema
from backend.app.persistence.transaction import unit_of_work

pytestmark=pytest.mark.db_integration


@pytest.fixture(scope='module')
def document_database(initial_database):
    auth,migration=initial_database;db=auth[0];before=check_schema(db.engine,db.settings)
    assert migrate(migration,target='AF004')['revision']=='AF004'
    assert migrate(migration,target='AF004')['revision']=='AF004'
    assert migrate(migration,target='AF003')['revision']=='AF004'
    assert check_schema(db.engine,db.settings)==before
    with unit_of_work(db.engine) as c:
        check_app_permissions(c);assert check_document_extension(c)['revision']=='AF004'
        assert check_criteria_extension(c)['revision']=='AF004'
    yield auth,migration


@pytest.fixture
def semantic(runtime):
    r=runtime;config=load_config('deploy/qwen-a02/worker-config.example.json')
    config=config.model_copy(update={'pdf_extraction':PDFExtractionConfiguration(mode='qwen',endpoint='https://qwen.invalid/v1/chat/completions')})
    app=create_postgres_app(r.settings,config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(app,base_url='http://localhost:8000') as client:
        client.headers['Origin']='http://localhost:8000';client.cookies.update(r.client.cookies)
        yield SimpleNamespace(**(vars(r)|{'client':client,'app':app,'config':config}))


def register(r,text=BRIEF,key=None,**extra):
    return r.client.post(r.base+'/text-documents',json={'text':text,**extra},headers={**csrf(r.client),'Idempotency-Key':str(key or uuid4())})


def run_extraction(r,record,reply=None):
    response=r.client.post(r.base+'/document-versions/'+record['document']['document_version_id']+'/extractions',
        headers={**csrf(r.client),'Idempotency-Key':str(uuid4())})
    assert response.status_code==202,response.text
    rid=response.json()['context']['run_id'];storage=open_document_storage(r.root);calls=[]
    raw=json.dumps(reply or model_reply(),ensure_ascii=False)
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'model':r.config.pdf_extraction.model,
            'choices':[{'finish_reason':'stop','message':{'content':raw}}]})
    try:
        queue=PostgresQueue(r.app.state.framework);agents=build_postgres_agents(r.config,r.app.state.framework,storage)
        agents.extractor.provider=QwenProvider(r.config.pdf_extraction,transport=httpx.MockTransport(respond))
        claim=queue.claim_next();assert claim is not None
        outcome=execute_claim(queue,claim,agents)
    finally:storage.close()
    assert outcome=='completed',r.client.get('/api/framework/v1/runs/'+rid).json().get('error')
    return rid,r.client.get('/api/framework/v1/runs/'+rid).json(),outcome,calls


def test_free_input_preserves_bytes_version_hash_idempotency_and_access(semantic):
    r=semantic;key=uuid4();response=register(r,key=key);assert response.status_code==201,response.text
    record=response.json();ref=record['document'];assert ref['media_type']=='text/plain' and ref['page_count']==1
    assert ref['sha256']==sha256(BRIEF.encode()).hexdigest()
    assert register(r,key=key).json()==record
    assert register(r,text=BRIEF+'\n변경',key=key).status_code==409
    content=r.client.get(r.base+'/document-versions/'+ref['document_version_id']+'/content')
    assert content.content==BRIEF.encode() and content.headers['content-type'].startswith('text/plain')
    other=email_user(r.auth_database)
    other_app=create_postgres_app(r.settings,r.config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(other_app,base_url='http://localhost:8000') as client:
        client.headers['Origin']='http://localhost:8000';assert login(client,email=other['email']).status_code==200
        assert client.get(r.base+'/document-versions/'+ref['document_version_id']).status_code==404
    assert r.client.get('/api/framework/v1/health').json()['free_text_registration']


@pytest.mark.parametrize('text,status',[('',422),('  ',422),('x\x00y',400)])
def test_invalid_free_input_does_not_create_a_version(semantic,text,status):
    r=semantic
    with unit_of_work(r.db.engine) as c:before=c.execute(select(func.count()).select_from(versions)).scalar_one()
    assert register(r,text=text).status_code==status
    with unit_of_work(r.db.engine) as c:assert c.execute(select(func.count()).select_from(versions)).scalar_one()==before


@pytest.mark.parametrize('text',['A','가','행사명: 새벽\r\n장소: 동쪽 공원'])
def test_short_and_crlf_inputs_are_preserved_without_pdf_conversion(semantic,text):
    r=semantic;response=register(r,text=text);assert response.status_code==201,response.text
    record=response.json();assert record['document']['sha256']==sha256(text.encode()).hexdigest()
    assert record['byte_size']==len(text.encode())
    assert r.client.get(r.base+'/document-versions/'+record['document']['document_version_id']+'/content').content==text.encode()


def test_free_input_requires_existing_session_and_csrf(semantic):
    r=semantic
    assert r.client.post(r.base+'/text-documents',json={'text':BRIEF},headers={'Idempotency-Key':str(uuid4())}).status_code==403
    other=create_postgres_app(r.settings,r.config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(other,base_url='http://localhost:8000') as client:
        client.headers['Origin']='http://localhost:8000'
        response=client.post(r.base+'/text-documents',json={'text':BRIEF},headers={**csrf(client),'Idempotency-Key':str(uuid4())})
        assert response.status_code==401


def create_version(r,rid,draft):
    response=r.client.post('/api/framework/v1/runs/'+rid+'/criteria-versions',json={
        'parent_version_id':None,'expected_base_sha256':digest(draft),'purpose':'synthetic_test','edits':[]},
        headers={**csrf(r.client),'Idempotency-Key':str(uuid4())})
    assert response.status_code==201,response.text
    return response.json()


def approve(r,rid,version):
    return r.client.post('/api/framework/v1/runs/'+rid+'/criteria-approvals',json={
        'criteria_version_id':version['version']['criteria_version_id'],'expected_sha256':version['sha256'],
        'purpose':'synthetic_test','reason':'Scripted isolated test, not a client approval.','reviewed_all_criteria':True},
        headers={**csrf(r.client),'Idempotency-Key':str(uuid4())})


@pytest.mark.parametrize('canonical_dates',[False,True])
def test_semantic_free_input_worker_storage_approval_and_restart_readback(semantic,canonical_dates):
    r=semantic;record=register(r).json();reply=model_reply()
    if canonical_dates:
        reply['requirements'][1]['expected']='2026-10-23';reply['requirements'][2]['expected']='2026-10-25'
    rid,saved,outcome,calls=run_extraction(r,record,reply)
    assert outcome=='completed' and len(calls)==1 and saved['execution_status']=='waiting_criteria',saved.get('error')
    draft=saved['criteria'];assert len(draft['criteria'])==5
    assert draft['extraction']['input_kind']=='free_text' and draft['extraction']['policy']=='a02-semantic-v3'
    assert draft['criteria'][2]['details']['normalized_expected']=='2026-10-25'
    if canonical_dates:
        assert draft['criteria'][2]['expected']=='2026-10-25'
        assert draft['criteria'][2]['details']['expected_source']['quote']=='10.25'
    assert draft['criteria'][-1]['details']['target']=='poster_visual' and draft['criteria'][-1]['expected']=='김천시 로고'
    version=create_version(r,rid,draft);assert not version['approval_blockers']
    response=approve(r,rid,version);assert response.status_code==200,response.text
    approved=response.json();assert approved['criteria']['version']['synthetic']
    app=create_postgres_app(r.settings,r.config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(app,base_url='http://localhost:8000') as restarted:
        restarted.cookies.update(r.client.cookies)
        route='/api/framework/v1/runs/'+rid
        assert restarted.get(route+'/criteria-approval').json()==approved
        current=restarted.get(route).json();assert current['execution_status']=='waiting_asset' and current['criteria']==draft
    with unit_of_work(r.db.engine) as c:
        step=c.execute(select(steps).where(steps.c.run_id==rid)).mappings().one()
        assert step['output_json']==draft and step['output_sha256']==digest(draft)


def test_new_text_version_invalidates_approval_of_a_stale_document(semantic):
    r=semantic;record=register(r).json();rid,saved,outcome,_=run_extraction(r,record)
    assert outcome=='completed',saved.get('error')
    new=r.client.post(r.base+'/text-documents/'+record['document_id']+'/versions',
        params={'previous_version_id':record['document']['document_version_id']},json={'text':BRIEF+'\n수정'},
        headers={**csrf(r.client),'Idempotency-Key':str(uuid4())})
    assert new.status_code==201,new.text
    assert new.json()['version_no']==2 and new.json()['previous_version_id']==record['document']['document_version_id']
    response=r.client.post('/api/framework/v1/runs/'+rid+'/criteria-versions',json={'parent_version_id':None,
        'expected_base_sha256':digest(saved['criteria']),'purpose':'synthetic_test','edits':[]},
        headers={**csrf(r.client),'Idempotency-Key':str(uuid4())})
    assert response.status_code==409 and response.json()['error']['code']=='CRITERIA_DOCUMENT_STALE'


def test_pending_scope_amendment_roundtrips_with_approval_evidence(semantic):
    from tests.agent_framework.test_criteria_review import edit
    r=semantic;record=register(r,text='행사명: 가을 축제\n관람시간은 확인 중입니다.').json()
    rid,saved,_,_=run_extraction(r,record,{'requirements':[requirement(1,'event_name','가을 축제')]})
    route='/api/framework/v1/runs/'+rid;draft=saved['criteria'];pending=draft['extraction']['unresolved_criterion_ids'][0]
    amendment=edit(pending,expected='10:00~18:00')
    body={'parent_version_id':None,'expected_base_sha256':digest(draft),'purpose':'synthetic_test','edits':[amendment]}
    # Partial classifications are rejected at the API boundary.
    amendment['resolution']['target']='poster_text'
    assert r.client.post(route+'/criteria-versions',json=body,headers={**csrf(r.client),'Idempotency-Key':str(uuid4())}).status_code==422
    amendment['resolution']['field']='viewing_hours'
    headers={**csrf(r.client),'Idempotency-Key':str(uuid4())}
    response=r.client.post(route+'/criteria-versions',json=body,headers=headers)
    assert response.status_code==201,response.text
    first=response.json();assert not first['approval_blockers']
    assert r.client.post(route+'/criteria-versions',json=body,headers=headers).json()==first
    assert r.client.get(route+'/criteria-review').json()['latest_version']==first
    body.update(parent_version_id=first['version']['criteria_version_id'],expected_base_sha256=first['sha256'],
        edits=[edit(pending,expected='09:00~17:00')])
    response=r.client.post(route+'/criteria-versions',json=body,headers={**csrf(r.client),'Idempotency-Key':str(uuid4())})
    assert response.status_code==201,response.text
    second=response.json();criterion=next(c for c in second['version']['criteria'] if c['criterion_id']==pending)
    assert criterion['details']['target']=='poster_text' and criterion['details']['field']=='viewing_hours'
    assert criterion['source']==next(c['source'] for c in draft['criteria'] if c['criterion_id']==pending)
    approved=approve(r,rid,second);assert approved.status_code==200,approved.text
    evidence=next(e for e in approved.json()['evidence']['evidence'] if pending in e['criterion_ids'])
    assert evidence['value_source']==criterion['value_source']
    assert r.client.get(route).json()['criteria']==draft
    assert r.client.get(route+'/criteria-approval').json()==approved.json()


def test_semantic_pdf_uses_same_worker_and_keeps_missing_condition_unapproved(semantic):
    r=semantic;pdf=next((PDFS/'EXH-NEW004').glob('*.pdf'))
    response=upload(r,pdf.read_bytes(),filename=pdf.name);assert response.status_code==201,response.text
    item=requirement(1,'venue','열린문화공간 후소 전시실')
    item['source']={'start_line':'p1-text:L31','end_line':'p1-text:L32'}
    rid,snapshot,outcome,_=run_extraction(r,response.json(),{'requirements':[item]})
    assert outcome=='completed' and snapshot['criteria']['extraction'].get('input_kind','pdf')=='pdf'
    draft=snapshot['criteria'];assert draft['criteria'][0]['expected']=='열린문화공간 후소 전시실'
    assert draft['extraction']['unresolved_criterion_ids']
    version=create_version(r,rid,draft);assert version['approval_blockers']
    response=approve(r,rid,version)
    assert response.status_code==409 and response.json()['error']['code']=='CRITERIA_UNRESOLVED'


def test_replacement_requirement_is_a_new_approved_version_with_preserved_history(semantic):
    from tests.agent_framework.test_criteria_review import edit
    r=semantic;record=register(r,text='제목: 밤의 도서관\n허용 범위: 공백 차이를 허용한다.').json()
    rid,saved,_,_=run_extraction(r,record,{'requirements':[requirement(1,'title_phrase','밤의 도서관')]})
    route='/api/framework/v1/runs/'+rid;first=create_version(r,rid,saved['criteria']);old=first['version']['criteria'][0]
    assert old['details']['qualifiers']
    change=edit(old['criterion_id'],expected='밤의 도서관');change['resolution'].update(
        target='poster_text',field='title_phrase',replacement_text='제목은 밤의 도서관으로 표기한다.')
    change['source']['quote']='담당자 회신: 제목은 밤의 도서관으로 표기한다.'
    body={'parent_version_id':first['version']['criteria_version_id'],'expected_base_sha256':first['sha256'],
        'purpose':'synthetic_test','edits':[change]}
    response=r.client.post(route+'/criteria-versions',json=body,headers={**csrf(r.client),'Idempotency-Key':str(uuid4())})
    assert response.status_code==201,response.text
    second=response.json();current=second['version']['criteria'][0]
    assert current['text']==change['resolution']['replacement_text'] and not current['details']['qualifiers']
    assert current['source']==old['source'] and current['value_source']['quote']==change['source']['quote']
    approved=approve(r,rid,second);assert approved.status_code==200,approved.text
    assert approved.json()['criteria']==second
    assert r.client.get(route+'/criteria-versions/'+first['version']['criteria_version_id']).json()==first
    assert r.client.get(route).json()['criteria']==saved['criteria']


def test_model_paragraphs_persist_and_project_to_frontend_without_approval(semantic):
    from tests.agent_framework.test_requirements_presentation import presentation_reply
    r=semantic
    config=r.config.model_copy(update={'pdf_extraction':r.config.pdf_extraction.model_copy(update={'presentation':True})})
    app=create_postgres_app(r.settings,config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(app,base_url='http://localhost:8000') as client:
        client.headers['Origin']='http://localhost:8000';client.cookies.update(r.client.cookies)
        current=SimpleNamespace(**(vars(r)|{'client':client,'app':app,'config':config}))
        assert client.get('/api/framework/v1/health').json()['requirements_presentation']
        listed=client.get('/api/framework/v1/projects?limit=100')
        assert listed.status_code==200 and any(p['id']==str(r.pid) for p in listed.json()['items'])
        token=client.get('/api/framework/v1/csrf').json()['csrf_token']
        response=client.post(current.base+'/text-documents',json={'text':BRIEF},headers={'X-CSRFToken':token,'Idempotency-Key':str(uuid4())})
        assert response.status_code==201,response.text
        rid,snapshot,_,calls=run_extraction(current,response.json(),presentation_reply())
        assert len(calls)==1 and snapshot['criteria']['extraction']['policy']=='a02-semantic-v4'
        path='/api/framework/v1/runs/'+rid+'/requirements'
        response=client.get(path);assert response.status_code==200,response.text
        projected=response.json()
        assert len(projected['conditions'])==4 and projected['conditions'][1]['criterion_ids']==['c002','c003']
        assert projected['approved'] is False and not snapshot['approvals']
        assert client.get('/api/framework/v1/runs/'+rid).json()['criteria']['criteria'][0]['details']['presentation']['value']=='김천김밥축제'
    restored=create_postgres_app(r.settings,config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(restored,base_url='http://localhost:8000') as client:
        client.cookies.update(r.client.cookies)
        assert client.get(path).json()==projected
        client.cookies.clear()
        assert client.get(path).status_code==401


def test_recorded_real_qwen_pdf_paragraphs_replay_through_postgres_worker(semantic):
    from pathlib import Path
    from hashlib import sha256
    from tests.agent_framework.replay_contract import current_contract_copy
    r=semantic
    config=r.config.model_copy(update={'pdf_extraction':r.config.pdf_extraction.model_copy(update={'presentation':True})})
    app=create_postgres_app(r.settings,config,engine=r.db.engine,pdf_storage_root=r.root)
    recorded=json.loads((Path(__file__).parent/'fixtures/a02_presentation_pdf_response.json').read_text())
    pdf=next((PDFS/'EXH-NEW004').glob('*.pdf'));raw=pdf.read_bytes()
    assert sha256(raw).hexdigest()==recorded['_meta']['source_sha256']
    with TestClient(app,base_url='http://localhost:8000') as client:
        client.headers['Origin']='http://localhost:8000';client.cookies.update(r.client.cookies)
        current=SimpleNamespace(**(vars(r)|{'client':client,'app':app,'config':config}))
        uploaded=upload(current,raw,filename=pdf.name);assert uploaded.status_code==201,uploaded.text
        # Preserve the v2 model recording; replay only its copied version tags at v3.
        rid,snapshot,_,calls=run_extraction(current,uploaded.json(),current_contract_copy(recorded['response']))
        assert len(calls)==1 and len(snapshot['criteria']['criteria'])==13
        response=client.get('/api/framework/v1/runs/'+rid+'/requirements')
        assert response.status_code==200,response.text
        result=response.json()
        assert len(result['conditions'])==12 and len(result['pending'])==1
        assert '가로: 3000픽셀' in [c['text'] for c in result['conditions']]
        assert '세로: 3000픽셀' in [c['text'] for c in result['conditions']]
        assert not snapshot['approvals'] and snapshot['execution_status']=='waiting_criteria'
