"""Actual file/DB pipeline with explicitly scripted observations, not Qwen/OCR."""
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import os,subprocess,sys
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,update,func

from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database,csrf
from tests.app.auth_fixtures import auth_environment
from tests.agent_framework.test_pdf_postgres_integration import document_database as initial_pdf_database,runtime,upload
from tests.agent_framework.test_criteria_postgres_integration import document_database
from tests.agent_framework.test_a02_postgres_integration import a02,register,admit,worker as work_pdf
from backend.agent_framework.composition import load_config,TeamConfiguration
from backend.agent_framework.a02 import build_postgres_agents
from backend.agent_framework.contracts import Approval,AssetRef
from backend.agent_framework.postgres import PostgresStore,runs,jobs,steps,digest
from backend.agent_framework.poster_review import read_registered_poster,poster_metadata,open_poster_storage
from backend.agent_framework.postgres_api import create_postgres_app
from backend.agent_framework.durable_worker import PostgresQueue,execute_claim
from backend.agent_framework.invocation import invoke_role
from backend.app.persistence.database import PersistenceError
from backend.app.persistence.transaction import unit_of_work
from backend.app.services.business import Business
from backend.app.services.files import Files
from backend.app.storage.local import LocalStorage
from examples.agent_framework.poster_testdata import generate,fields

pytestmark=pytest.mark.db_integration
ROOT=Path(__file__).resolve().parents[2]


def post(client,path,body,key=None):
    return client.post(path,json=body,headers={**csrf(client),'Idempotency-Key':str(key or uuid4())})


@pytest.fixture
def pipeline(a02,tmp_path,monkeypatch,request):
    r=a02;case=getattr(request,'param','EXH-NEW004');registered,raw=register(r,case)
    source=admit(r,registered).json();source_id=source['context']['run_id'];assert work_pdf(r)[0]=='completed'
    base_route='/api/framework/v1/runs/'+source_id
    source=r.client.get(base_route).json();draft=source['criteria']
    expected=fields(case,'normal');edits=[]
    for i,criterion in enumerate(draft['criteria']):
        field=('title','date','notice')[i] if i<3 else None
        edit={'criterion_id':criterion['criterion_id'],'action':'amend' if field else 'exclude',
            'reason':'Synthetic three-field wiring scope; not real client approval.',
            'source':{'reference':'synthetic:poster-test:'+(field or 'excluded'),
                'quote':expected[field] if field else 'Synthetic test excludes other conditions explicitly.'}}
        if field:edit['resolution']={'kind':'text','operator':'contains','expected':expected[field],'required':True,'unit':None}
        edits.append(edit)
    overview=r.client.get(base_route+'/criteria-review').json()
    version=post(r.client,base_route+'/criteria-versions',{'parent_version_id':None,
        'expected_base_sha256':overview['source_draft_sha256'],'purpose':'synthetic_test','edits':edits})
    assert version.status_code==201,version.text
    version=version.json();approved=post(r.client,base_route+'/criteria-approvals',{
        'criteria_version_id':version['version']['criteria_version_id'],'expected_sha256':version['sha256'],
        'purpose':'synthetic_test','reason':'Scripted development approval only.','reviewed_all_criteria':True})
    assert approved.status_code==200,approved.text
    fixture_dir=tmp_path/'poster-fixtures';manifest=generate(fixture_dir,cases=(case,))
    monkeypatch.setenv('FRAMEWORK_POSTER_TEST_MANIFEST',str((fixture_dir/'manifest.json').resolve()))
    config_data=load_config('examples/agent_framework/team_config.poster-review.template.json').model_dump(mode='json')
    config_data['roles']['A04_A05']['factory']='tests.agent_framework.poster_test_provider:make_inspector'
    config=TeamConfiguration.model_validate(config_data)
    poster_root=tmp_path.resolve()/'m008-images';LocalStorage.initialize(poster_root)
    app=create_postgres_app(r.settings,config,engine=r.db.engine,pdf_storage_root=r.root,poster_storage_root=poster_root)
    with TestClient(app,base_url='http://localhost:8000') as client:
        client.cookies.update(r.client.cookies);client.headers['Origin']='http://localhost:8000'
        store=app.state.framework;business=Business(r.db.engine,app.state.authorization,r.settings)
        files=Files(r.db.engine,app.state.poster_reviews.storage,business)
        yield SimpleNamespace(**(vars(r)|{'client':client,'app':app,'store':store,'config':config,'case':case,
            'source_id':source_id,'source_route':base_route,'approved':approved.json(),'baseline':version,
            'registered':registered,'source_raw':raw,'fixture_dir':fixture_dir,'manifest':manifest,
            'poster_root':poster_root,'files':files,'queue':PostgresQueue(store)}))


def image(r,variant):
    record=next(row for row in r.manifest['records'] if row['variant']==variant)
    return r.files.upload(r.subject,False,r.pid,BytesIO((r.fixture_dir/record['png']).read_bytes()),str(uuid4()))


def review_body(r,img):
    return {'criteria_version_id':r.baseline['version']['criteria_version_id'],
        'expected_criteria_sha256':r.baseline['sha256'],'asset_version_id':img['id']}


def create_review(r,img,key=None):
    response=post(r.client,r.source_route+'/poster-reviews',review_body(r,img),key)
    assert response.status_code==201,response.text
    return response.json()


def approve(r,snapshot,decision,**extra):
    return post(r.client,'/api/framework/v1/runs/'+snapshot['context']['run_id']+'/approvals',{
        'context':snapshot['context'],'actor_id':snapshot['context']['actor_id'],'decision':decision,
        'reason':'Synthetic scripted development decision only.'}|extra)


def run_worker(r,change=None):
    pdf=r.app.state.documents.storage;posters=r.app.state.poster_reviews.storage
    agents=build_postgres_agents(r.config,r.store,pdf,poster_storage=posters)
    if change:change(agents)
    claim=r.queue.claim_next();assert claim is not None
    outcome=execute_claim(r.queue,claim,agents)
    return r.store.get(r.subject,claim.run_id).model_dump(mode='json'),outcome,claim


@pytest.mark.parametrize('pipeline',('EXH-NEW004','CON-NEW043','FES-NEW072'),indirect=True)
def test_normal_wrong_date_missing_notice_and_corrected_revision_share_exact_baseline(pipeline):
    r=pipeline;outcomes={}
    for variant,expected in (('normal','pass'),('wrong_date','fail'),('missing_notice','fail')):
        img=image(r,variant);new=create_review(r,img);rid=new['context']['run_id']
        assert new['execution_status']=='waiting_criteria' and not new['approvals'] and new['evidence'] is None
        assert r.queue.claim_next() is None  # No inherited input confirmation.
        assert new['baseline']['criteria_sha256']==r.baseline['sha256']
        assert new['baseline']['approval_id']==r.approved['approval_id']
        assert approve(r,new,'approve_criteria').status_code==200
        result,outcome,_=run_worker(r)
        assert outcome=='completed' and result['machine_verdict']==expected and result['synthetic']
        assert [s['role'] for s in result['step_records']]==['A02','A04_A05','A06','A07']
        assert result['step_records'][0]['status']=='reused' and result['step_records'][0]['duration_ms'] is None
        assert all(s['status']=='completed' for s in result['step_records'][1:])
        assert result['criteria']['criteria']==r.baseline['version']['criteria']
        assert all(e['value_source'] for e in result['evidence']['evidence'])
        outcomes[variant]=result
        if expected=='pass':assert approve(r,result,'approve_final').json()['execution_status']=='completed'
        else:assert approve(r,result,'approve_final').status_code==409
    assert any('value_mismatch' in item['issues'] for item in outcomes['wrong_date']['validated']['items'])
    parent=outcomes['wrong_date'];revised_img=image(r,'revised')
    path='/api/framework/v1/runs/'+parent['context']['run_id']+'/poster-revisions'
    assert post(r.client,path,review_body(r,revised_img)).status_code==409
    assert approve(r,parent,'approve_changes').status_code==200
    old_image={'id':parent['context']['asset_version_id']}
    assert post(r.client,path,review_body(r,old_image)).status_code==409
    revision_key=uuid4()
    response=post(r.client,path,review_body(r,revised_img),revision_key);assert response.status_code==201,response.text
    child=response.json()
    assert post(r.client,path,review_body(r,revised_img),revision_key).json()==child
    assert post(r.client,path,review_body(r,revised_img)).status_code==409
    from backend.app.security.errors import AuthError
    from backend.agent_framework.contracts import Context
    with unit_of_work(r.db.engine) as c:reference,_=poster_metadata(c,r.pid,UUID(revised_img['id']))
    with pytest.raises(AuthError) as rejected:
        r.store.rereview(r.subject,parent['context']['run_id'],Context.model_validate(child['context']),reference)
    assert rejected.value.code=='USE_POSTER_REVISION_ENDPOINT'
    assert child['context']['parent_run_id']==parent['context']['run_id'] and child['baseline']==parent['baseline']
    assert child['context']['asset_version_id']!=parent['context']['asset_version_id']
    assert not child['approvals'] and child['execution_status']=='waiting_criteria' and r.queue.claim_next() is None
    assert approve(r,child,'approve_criteria').status_code==200
    result,outcome,_=run_worker(r)
    assert outcome=='completed' and result['machine_verdict']=='pass'
    assert approve(r,result,'approve_final').json()['execution_status']=='completed'
    app=create_postgres_app(r.settings,r.config,engine=r.db.engine,pdf_storage_root=r.root,poster_storage_root=r.poster_root)
    with TestClient(app,base_url='http://localhost:8000') as restarted:
        restarted.cookies.update(r.client.cookies)
        saved=restarted.get('/api/framework/v1/runs/'+child['context']['run_id']).json()
        assert saved['execution_status']=='completed' and saved['baseline']==child['baseline']
        assert restarted.get(r.source_route+'/criteria-approval').json()==r.approved


@pytest.mark.parametrize('pipeline',('EXH-NEW004','CON-NEW043','FES-NEW072'),indirect=True)
def test_preserved_real_poster_files_are_registered_and_verified_without_claiming_a_model_verdict(pipeline):
    from PIL import Image
    from backend.app.security.errors import AuthError
    r=pipeline;workflow=ROOT/'docs/poster-baselines/2026-11-03/workflow'
    scenarios=json.loads((workflow/'01_시나리오_설계.json').read_text())
    case=next(c for c in scenarios['cases'] if c['id']==r.case)
    raw=(workflow/case['reference_poster']).read_bytes();assert sha256(raw).hexdigest()==case['reference_poster_sha256']
    with Image.open(BytesIO(raw)) as original:width,height=original.size
    if max(width,height)>8192 or width*height>10000000:
        # Preserve both source and the existing product's admission limit.
        with pytest.raises(AuthError) as error:r.files.upload(r.subject,False,r.pid,BytesIO(raw),str(uuid4()))
        assert error.value.code=='IMAGE_LIMIT_EXCEEDED'
        assert sha256((workflow/case['reference_poster']).read_bytes()).hexdigest()==case['reference_poster_sha256']
        assert r.queue.claim_next() is None
        return
    img=r.files.upload(r.subject,False,r.pid,BytesIO(raw),str(uuid4()))
    snapshot=create_review(r,img)
    with unit_of_work(r.db.engine) as c:ref,_=poster_metadata(c,r.pid,UUID(img['id']))
    verified=read_registered_poster(r.store,r.app.state.poster_reviews.storage,
        r.store.get(r.subject,snapshot['context']['run_id']).context,ref,r.subject)
    assert verified.width==img['width'] and verified.height==img['height']
    assert sha256(verified.content).hexdigest()==img['display_sha256']
    assert snapshot['machine_verdict'] is None and snapshot['inspection'] is None and r.queue.claim_next() is None


def test_idempotency_and_body_cannot_change_the_approved_version_or_supply_observations(pipeline):
    r=pipeline;img=image(r,'normal');key=uuid4();first=create_review(r,img,key)
    assert create_review(r,img,key)==first
    assert post(r.client,r.source_route+'/poster-reviews',review_body(r,img)|{'expected_criteria_sha256':'0'*64}).status_code==409
    assert post(r.client,r.source_route+'/poster-reviews',review_body(r,img)|{'observed':'invented'}).status_code==422
    other=image(r,'wrong_date')
    assert post(r.client,r.source_route+'/poster-reviews',review_body(r,other),key).status_code==409
    assert r.queue.claim_next() is None


def test_poster_command_enforces_existing_session_csrf_and_write_permission(pipeline):
    from tests.app.test_v1_auth_integration import email_user
    from backend.app.persistence.schema.identity import project_members
    from sqlalchemy import insert
    r=pipeline;img=image(r,'normal');path=r.source_route+'/poster-reviews';body=review_body(r,img)
    assert r.client.post(path,json=body,headers={'Idempotency-Key':str(uuid4())}).status_code==403
    other=email_user(r.auth_database)
    with r.owner.begin() as c:
        c.execute(insert(project_members).values(project_id=r.pid,user_id=UUID(other['id']),role='owner'))
        c.execute(update(project_members).where(project_members.c.project_id==r.pid,
            project_members.c.user_id==r.subject.user_id).values(role='viewer'))
    assert post(r.client,path,body).status_code==403
    assert r.queue.claim_next() is None
    assert r.client.post('/api/v1/auth/logout',headers=csrf(r.client)).status_code==204
    assert post(r.client,path,body).status_code==401


def test_corrupt_image_fails_before_inspector_and_never_writes_a_pass(pipeline):
    from backend.app.services.business import table
    r=pipeline;img=image(r,'normal');snapshot=create_review(r,img)
    assert approve(r,snapshot,'approve_criteria').status_code==200
    with unit_of_work(r.db.engine) as c:
        path=c.execute(select(table('stored_files').c.storage_key).where(table('stored_files').c.file_id==
            c.execute(select(table('asset_versions').c.display_file_id).where(table('asset_versions').c.asset_version_id==UUID(img['id']))).scalar_one())).scalar_one()
    (r.poster_root/path).write_bytes(b'tampered');calls=[]
    def change(agents):agents.inspector.callback=SimpleNamespace(inspect_poster=lambda *_:calls.append(1))
    result,outcome,_=run_worker(r,change)
    assert outcome=='failed' and not calls and result['inspection'] is None and result['machine_verdict'] is None


def test_document_replacement_after_queueing_closes_job_before_any_dispatch(pipeline):
    r=pipeline;snapshot=create_review(r,image(r,'normal'));assert approve(r,snapshot,'approve_criteria').status_code==200
    response=upload(r,r.source_raw,document=r.registered['document_id'],previous=r.registered['document']['document_version_id'])
    assert response.status_code==201,response.text
    assert r.queue.claim_next() is None
    result=r.store.get(r.subject,snapshot['context']['run_id'])
    assert result.execution_status=='failed' and result.error.code=='CRITERIA_DOCUMENT_STALE'
    assert [s.role for s in result.step_records]==['A02']


def test_pdf_mutation_during_inspection_cannot_commit_a_successful_stage(pipeline):
    from backend.app.storage.local import key
    r=pipeline;snapshot=create_review(r,image(r,'normal'));assert approve(r,snapshot,'approve_criteria').status_code==200
    pdf_path=r.root/key(r.pid,UUID(r.registered['document']['file_id']))
    original=pdf_path.read_bytes()
    def change(agents):
        real=agents.inspector.callback
        def tamper(request,poster):
            result=real.inspect_poster(request,poster)
            pdf_path.write_bytes(b'changed during inspection')
            return result
        agents.inspector.callback=SimpleNamespace(inspect_poster=tamper)
    try:
        result,outcome,_=run_worker(r,change)
        assert outcome=='failed' and result['inspection'] is None and result['machine_verdict'] is None
    finally:pdf_path.write_bytes(original)


def test_committed_inspection_is_reused_after_safe_lease_expiry(pipeline):
    from datetime import timedelta
    r=pipeline;new=create_review(r,image(r,'normal'));assert approve(r,new,'approve_criteria').status_code==200
    agents=build_postgres_agents(r.config,r.store,r.app.state.documents.storage,poster_storage=r.app.state.poster_reviews.storage)
    claim=r.queue.claim_next();req=r.queue.begin_step(claim,'A04_A05')
    result=invoke_role('A04_A05',agents.inspector,req);r.queue.complete_step(claim,'A04_A05',req,result,1)
    with r.owner.begin() as c:c.execute(update(jobs).where(jobs.c.job_id==claim.job_id).values(lease_expires_at=func.clock_timestamp()-timedelta(seconds=1)))
    assert r.queue.recover_expired()[new['context']['run_id']]=='queued'
    def fail(*_):raise AssertionError('Committed inspector must not be called again')
    agents.inspector.callback=SimpleNamespace(inspect_poster=fail)
    assert execute_claim(r.queue,r.queue.claim_next(),agents)=='completed'
    assert r.store.get(r.subject,new['context']['run_id']).machine_verdict=='pass'


def test_separate_worker_process_uses_both_existing_storage_roots(pipeline,tmp_path):
    r=pipeline;new=create_review(r,image(r,'normal'));assert approve(r,new,'approve_criteria').status_code==200
    envfile=tmp_path/'worker.env';configfile=tmp_path/'poster-config.json'
    envfile.write_text(''.join(k+'='+str(v)+'\n' for k,v in auth_environment(r.db).items()));envfile.chmod(0o600)
    configfile.write_text(r.config.model_dump_json(indent=2))
    try:
        done=subprocess.run([sys.executable,'-m','backend.agent_framework','worker','--env-file',str(envfile),
            '--config',str(configfile),'--pdf-storage-root',str(r.root),'--poster-storage-root',str(r.poster_root),'--once'],
            capture_output=True,text=True,timeout=30)
    finally:envfile.unlink()
    assert done.returncode==0,(done.stdout,done.stderr)
    saved=r.store.get(r.subject,new['context']['run_id'])
    assert saved.machine_verdict=='pass' and saved.synthetic
