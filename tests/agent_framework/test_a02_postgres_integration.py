"""Registered text PDF -> independent worker -> stored unapproved draft.

Synthetic/mock provider only. All fixtures use NEW disposable PostgreSQL DBs.
"""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select,update

from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database,csrf
from tests.app.auth_fixtures import auth_environment
from tests.agent_framework.test_pdf_postgres_integration import document_database,runtime,upload,PDFS
from backend.agent_framework.a02 import build_postgres_agents,QwenProvider,QWEN_PROMPT_HASH
from backend.agent_framework.a02_configuration import PDFExtractionConfiguration
from backend.agent_framework.composition import load_config
from backend.agent_framework.contracts import FrameworkError
from backend.agent_framework.documents import open_document_storage
from backend.agent_framework.durable_worker import PostgresQueue,execute_claim
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.postgres import PostgresStore,runs,jobs,steps,approvals,digest
from backend.agent_framework.postgres_api import create_postgres_app
from backend.app.persistence.transaction import unit_of_work

pytestmark=pytest.mark.db_integration


@pytest.fixture
def a02(runtime):
    r=runtime;config=load_config('examples/agent_framework/team_config.pdf-mock.json')
    app=create_postgres_app(r.settings,config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(app,base_url='http://localhost:8000') as client:
        client.cookies.update(r.client.cookies);client.headers['Origin']='http://localhost:8000'
        data=vars(r)|{'app':app,'client':client,'config':config,'store':app.state.framework}
        current=SimpleNamespace(**data)
        yield current
        with r.owner.begin() as c:
            queue=PostgresQueue(current.store)
            pending=c.execute(select(jobs).where(jobs.c.project_id==r.pid,jobs.c.state.in_(('queued','running')))).mappings().all()
            for job in pending:
                row=c.execute(select(runs).where(runs.c.run_id==job['run_id'])).mappings().one()
                queue._close(c,dict(row),dict(job),FrameworkError(code='TEST_CLEANUP',message='Synthetic test cleanup.'))


def register(r,case='EXH-NEW004'):
    p=next((PDFS/case).glob('*.pdf'));raw=p.read_bytes()
    response=upload(r,raw,filename=p.name);assert response.status_code==201,response.text
    return response.json(),raw


def admit(r,record,key=None,**kwargs):
    return r.client.post(r.base+'/document-versions/'+record['document']['document_version_id']+'/extractions',
        headers={**csrf(r.client),'Idempotency-Key':str(key or uuid4())},**kwargs)


def worker(r,*,change=None):
    storage=open_document_storage(r.root)
    try:
        store=PostgresStore(r.db.engine,r.config)
        queue=PostgresQueue(store);agents=build_postgres_agents(r.config,store,storage)
        if change:change(agents)
        claim=queue.claim_next();assert claim is not None
        return execute_claim(queue,claim,agents),claim,queue
    finally:storage.close()


@pytest.mark.parametrize('case',('EXH-NEW004','CON-NEW043','FES-NEW072'))
def test_registered_sample_runs_pdf_only_and_reads_persisted_trace_after_restart(a02,case):
    r=a02;record,raw=register(r,case);queued=admit(r,record)
    assert queued.status_code==202,queued.text
    snapshot=queued.json();rid=snapshot['context']['run_id']
    assert snapshot['context']['asset_version_id'] is None and snapshot['criteria'] is None
    assert snapshot['execution_status']=='queued' and snapshot['synthetic'] is True
    outcome,claim,queue=worker(r);assert outcome=='completed'
    app=create_postgres_app(r.settings,r.config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(app,base_url='http://localhost:8000') as restarted:
        restarted.cookies.update(r.client.cookies)
        result=restarted.get('/api/framework/v1/runs/'+rid).json()
        assert result['execution_status']=='waiting_criteria'
        assert result['machine_verdict'] is None and not result['approvals']
        draft=result['criteria'];trace=draft['extraction']
        assert result['synthetic'] and draft['synthetic'] and trace['mode']=='mock'
        assert trace['document_sha256']==sha256(raw).hexdigest()
        assert len(trace['sources'])==2 and trace['unresolved_criterion_ids']
        assert result['step_records'][0]['role']=='A02' and result['step_records'][0]['status']=='completed'
        assert restarted.get('/api/framework/v1/runs/'+rid+'/approvals').json()==[]
    with unit_of_work(r.db.engine) as c:
        row=c.execute(select(runs).where(runs.c.run_id==rid)).mappings().one()
        assert row['input_json']['asset'] is None and row['input_sha256']==digest(row['input_json'])
        step=c.execute(select(steps).where(steps.c.run_id==rid)).mappings().one()
        assert step['output_json']==draft and step['output_sha256']==digest(draft)
        assert not c.execute(select(approvals).where(approvals.c.run_id==rid)).all()
    assert queue.claim_next() is None


def test_admission_idempotency_and_input_boundaries_do_not_forge_assets_or_approvals(a02):
    r=a02;record,_=register(r);key=uuid4();reply=admit(r,record,key)
    assert r.client.get('/api/framework/v1/runs/'+reply.json()['context']['run_id']+'/criteria-review').status_code==503
    assert reply.status_code==202,reply.text
    assert admit(r,record,key).json()==reply.json()
    second,_=register(r,'FES-NEW072')
    assert admit(r,second,key).status_code==409
    assert admit(r,record,json={'expected':'client cannot submit answers'}).status_code==422
    worker(r);rid=reply.json()['context']['run_id']
    snapshot=r.client.get('/api/framework/v1/runs/'+rid).json()
    response=r.client.post('/api/framework/v1/runs/'+rid+'/approvals',headers=csrf(r.client),
        json={'context':snapshot['context'],'actor_id':snapshot['context']['actor_id'],'decision':'approve_criteria'})
    assert response.status_code==409
    current=r.client.get('/api/framework/v1/runs/'+rid).json()
    assert current['execution_status']=='waiting_criteria' and not current['approvals']


def test_admission_rechecks_session_csrf_project_and_archive_access(a02):
    from backend.app.persistence.schema.identity import projects
    from sqlalchemy import func
    r=a02;record,_=register(r);vid=record['document']['document_version_id']
    route=r.base+'/document-versions/'+vid+'/extractions'
    assert r.client.post(route,headers={'Idempotency-Key':str(uuid4())}).status_code==403
    assert r.client.post('/api/framework/v1/projects/'+str(uuid4())+'/document-versions/'+vid+'/extractions',
        headers={**csrf(r.client),'Idempotency-Key':str(uuid4())}).status_code==404
    with r.owner.begin() as c:c.execute(update(projects).where(projects.c.project_id==r.pid).values(business_state='archived',archived_at=func.clock_timestamp()))
    assert admit(r,record).status_code==409
    assert r.client.post('/api/v1/auth/logout',headers=csrf(r.client)).status_code==204
    assert admit(r,record).status_code==401
    with unit_of_work(r.db.engine) as c:assert not c.execute(select(runs).where(runs.c.project_id==r.pid)).all()


def test_provider_runs_without_transaction_and_bad_source_never_saves_a_draft(a02):
    r=a02;record,_=register(r);rid=admit(r,record).json()['context']['run_id'];calls=[]
    def change(agents):
        def invalid(sources):
            calls.append(1);assert r.db.engine.pool.checkedout()==0
            return json.dumps({'candidates':[{'text':'fabricated','quote':'fabricated','kind':'text',
                'expected':'wrong','operator':'contains','unit':None,'required':True,'unresolved':False,
                'block_id':sources[0].block_id,'page':sources[0].page}]}).encode()
        agents.extractor.provider=invalid
    outcome,_,queue=worker(r,change=change)
    assert outcome=='failed' and len(calls)==1
    result=r.client.get('/api/framework/v1/runs/'+rid).json()
    assert result['criteria'] is None and result['error']['code']=='A02_SOURCE_MISMATCH'
    with unit_of_work(r.db.engine) as c:
        step=c.execute(select(steps).where(steps.c.run_id==rid)).mappings().one()
        assert step['output_json'] is None and step['state']=='failed'
    assert queue.claim_next() is None


def test_ambiguous_provider_call_is_terminal_without_automatic_replay(a02):
    r=a02;record,_=register(r);rid=admit(r,record).json()['context']['run_id'];calls=[]
    def change(agents):
        def timeout(_):
            calls.append(1)
            raise AdapterFailure('QWEN_OUTCOME_UNKNOWN','Synthetic ambiguous provider failure.',outcome_unknown=True)
        agents.extractor.provider=timeout
    outcome,_,queue=worker(r,change=change)
    assert outcome=='outcome_unknown' and len(calls)==1
    result=r.client.get('/api/framework/v1/runs/'+rid).json()
    assert result['criteria'] is None and result['execution_status']=='outcome_unknown'
    assert not queue.recover_expired() and queue.claim_next() is None


def test_original_pdf_tampering_prevents_any_provider_invocation(a02):
    from backend.agent_framework.documents import versions
    r=a02;record,_=register(r);rid=admit(r,record).json()['context']['run_id'];calls=[]
    with unit_of_work(r.db.engine) as c:
        path=c.execute(select(versions.c.storage_key).where(versions.c.document_version_id==UUID(record['document']['document_version_id']))).scalar_one()
    (r.root/path).write_bytes(b'%PDF-corrupt')
    def change(agents):agents.extractor.provider=lambda _:calls.append(1)
    outcome,_,_=worker(r,change=change)
    assert outcome=='failed' and not calls
    assert r.client.get('/api/framework/v1/runs/'+rid).json()['criteria'] is None


def test_separate_worker_cli_process_reads_registered_pdf_and_persists_output(a02,tmp_path):
    r=a02;record,_=register(r);rid=admit(r,record).json()['context']['run_id']
    envfile=tmp_path/'worker-test.env'
    envfile.write_text(''.join(k+'='+str(v)+'\n' for k,v in auth_environment(r.db).items()));envfile.chmod(0o600)
    try:
        result=subprocess.run([sys.executable,'-m','backend.agent_framework','worker','--env-file',str(envfile),
            '--config','examples/agent_framework/team_config.pdf-mock.json','--pdf-storage-root',str(r.root),'--once'],
            capture_output=True,text=True,timeout=30)
    finally:envfile.unlink()
    assert result.returncode==0,(result.stdout,result.stderr)
    assert 'SYNTHETIC A02 MOCK' in result.stderr
    assert 'completed' in result.stdout
    current=r.client.get('/api/framework/v1/runs/'+rid).json()
    assert current['execution_status']=='waiting_criteria' and current['criteria']['extraction']['mode']=='mock'


@pytest.mark.parametrize('value_selection',[False,True])
def test_qwen_line_selection_fake_transport_persists_exact_quote_and_original_reply_hash(a02,value_selection):
    r=a02;record,_=register(r);responses=[]
    config=r.config.model_copy(update={'pdf_extraction':PDFExtractionConfiguration(
        mode='qwen',structuring='literal_legacy',endpoint='https://qwen.invalid/v1/chat/completions')})
    app=create_postgres_app(r.settings,config,engine=r.db.engine,pdf_storage_root=r.root)
    def respond(request):
        body=json.loads(request.content)
        line=next(line for source in json.loads(body['messages'][1]['content'])['sources']
            for line in source['lines'] if ('3000픽셀' if value_selection else '표기한다') in line['text'])
        candidate={'start_line':line['id'],'end_line':line['id'],
            'kind':'unsupported','operator':'manual','expected':None,'required':True,'unit':None,'unresolved':False}
        if value_selection:
            number=next(p['id'] for p in line['parts'] if p['text']=='3000')
            unit=next(p['id'] for p in line['parts'] if p['text']=='픽셀')
            candidate.update(kind='number',operator='equals',expected={'start':number,'end':number},unit={'start':unit,'end':unit})
        raw=json.dumps({'candidates':[candidate]},ensure_ascii=False)
        responses.append((raw,line['text']))
        return httpx.Response(200,json={'model':config.pdf_extraction.model,
            'choices':[{'finish_reason':'stop','message':{'content':raw}}]})
    with TestClient(app,base_url='http://localhost:8000') as client:
        client.headers['Origin']='http://localhost:8000';client.cookies.update(r.client.cookies)
        reply=client.post(r.base+'/document-versions/'+record['document']['document_version_id']+'/extractions',
            headers={**csrf(client),'Idempotency-Key':str(uuid4())})
        assert reply.status_code==202,reply.text
        rid=reply.json()['context']['run_id'];store=app.state.framework;queue=PostgresQueue(store)
        storage=open_document_storage(r.root)
        try:
            agents=build_postgres_agents(config,store,storage)
            agents.extractor.provider=QwenProvider(config.pdf_extraction,transport=httpx.MockTransport(respond))
            claim=queue.claim_next();assert claim is not None and claim.run_id==rid
            assert execute_claim(queue,claim,agents)=='completed'
        finally:storage.close()
        saved=client.get('/api/framework/v1/runs/'+rid).json()
        assert saved['execution_status']=='waiting_criteria' and not saved['synthetic']
        assert len(responses)==1 and not saved['approvals'] and saved['machine_verdict'] is None
        draft=saved['criteria'];criterion=draft['criteria'][0];trace=draft['extraction']
        assert criterion['text']==criterion['source']['quote'] and responses[0][1] in criterion['text']
        assert criterion['source']['source_hash']==sha256(criterion['source']['quote'].encode()).hexdigest()
        assert trace['mode']=='qwen' and trace['prompt_sha256']==QWEN_PROMPT_HASH
        assert trace['policy']=='a02-source-compile-v2' and trace['unresolved_criterion_ids']
        assert trace['response_sha256']==sha256(responses[0][0].encode()).hexdigest()
        if value_selection:
            assert criterion['expected']=='3000' and criterion['unit']=='픽셀' and criterion['kind']=='number'
            assert [c['details']['field'] for c in draft['criteria'][:2]]==['pixel_width','pixel_height']
        else:
            assert [c['expected'] for c in draft['criteria'][:2]]==['수원의 영화','수원의 극장']
            assert all(c['operator']=='manual' for c in draft['criteria'][:2])
    app=create_postgres_app(r.settings,config,engine=r.db.engine,pdf_storage_root=r.root)
    with TestClient(app,base_url='http://localhost:8000') as restarted:
        restarted.cookies.update(r.client.cookies)
        assert restarted.get('/api/framework/v1/runs/'+rid).json()['criteria']==draft
