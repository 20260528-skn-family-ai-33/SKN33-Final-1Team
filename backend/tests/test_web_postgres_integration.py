"""Current UI endpoints, real restricted-role PostgreSQL and synthetic OCR only."""
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
import json
from uuid import UUID
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user,login,csrf
from backend.app.api import create_app
from backend.app.web_postgres import PostgresWebStore,CompletedWebExecutor
from backend.app.storage.local import LocalStorage
from backend.app.services.business import table,one
from backend.app.persistence.database import PersistenceError

pytestmark=pytest.mark.db_integration


def image():
    out=BytesIO();Image.new('RGB',(120,80),'white').save(out,format='PNG');return out.getvalue()


def criteria():
    condition=dict(label='행사명',requirement='합성 축제',level='필수',inspection='원문 확인',
                   sources=[dict(source_id='p1-s1',page=1,source='행사명: 합성 축제')])
    return [dict(id='title',text='행사명: 합성 축제',type='information',source='합성 PDF p.1',document_condition=condition),
            dict(id='file',text='파일 형식: PNG',type='information',source='합성 입력')]


def recognize(path):
    with Image.open(path) as im:size=list(im.size)
    return dict(status='success',engine='paddleocr',pipeline='paddle_full_v1',device='cpu',execution_target='local',
                input_dimensions=size,records=[dict(id='ocr-1',text='합성 축제',confidence=0.99,bbox_xyxy=[2,2,120,30],recognition_engine='paddleocr')])


@pytest.fixture
def context(auth_database,tmp_path,monkeypatch):
    monkeypatch.delenv('REVIEW_MODELS_URL',raising=False)
    monkeypatch.setenv('RUN_TARGET','local')
    root=tmp_path/'files';LocalStorage.initialize(root)
    history=tmp_path/'history';history.mkdir()
    preserved=history/'old-record';preserved.mkdir();(preserved/'result.json').write_text('{"status":"queued","protected":true}')
    settings=replace(auth_database[3],storage_root=root)
    user=email_user(auth_database)
    return dict(db=auth_database[0],owner=auth_database[1],settings=settings,user=user,history=history,preserved=preserved)


@contextmanager
def client_for(ctx,recognizer=recognize):
    store=PostgresWebStore(ctx['settings'],engine=ctx['db'].engine)
    app=create_app(recognizer=recognizer,runtime_status=lambda:{'status':'ready'},review_root=ctx['history'],postgres_store=store)
    with TestClient(app,base_url='http://localhost:8000',client=('127.0.0.1',12)) as client:
        client.headers['Origin']='http://localhost:8000'
        assert login(client,email=ctx['user']['email']).status_code==200
        yield client,store


def submit(client):
    return client.post('/api/reviews',headers=csrf(client),data=dict(criteria=json.dumps(criteria(),ensure_ascii=False),
        criteria_version='7',project_name='[합성 테스트] 신규 자동 저장'),files={'image':('test.png',image(),'image/png')})


def test_completed_review_is_normalized_in_pg_and_readable_without_json_and_after_restart(context):
    ctx=context;old=(ctx['preserved']/'result.json').read_bytes()
    with client_for(ctx) as (client,store):
        response=submit(client);assert response.status_code==202,response.text
        identifier=response.json()['id'];result=client.get('/api/reviews/'+identifier)
        assert result.status_code==200,result.text
        detail=result.json();assert detail['status']=='completed',detail
        assert len(detail['results'])==2 and detail['criteria_version']==7
        assert detail['criteria'][0]['document_condition']['sources'][0]['page']==1
        with ctx['db'].engine.connect() as c:
            run=one(c,'review_runs','run_id',UUID(identifier))
            assert run['origin_kind']=='service_execution' and run['execution_status']=='completed'
            assert run['requested_by']==UUID(ctx['user']['id'])
            version=one(c,'criteria_versions','criteria_version_id',run['criteria_version_id'])
            assert version['confirmation_state']=='confirmed' and version['confirmed_by']==UUID(ctx['user']['id'])
            row=c.execute(select(table('criteria_items')).where(table('criteria_items').c.criteria_version_id==version['criteria_version_id'],
                         table('criteria_items').c.external_item_id=='title')).mappings().one()
            assert row['expected_value']=='합성 축제' and row['source_locator']['sources'][0]['page']==1
            assert c.execute(select(func.count()).select_from(table('item_results')).where(table('item_results').c.run_id==run['run_id'])).scalar_one()==2
            assert c.execute(select(func.count()).select_from(table('ocr_runs')).where(table('ocr_runs').c.attempt_id==run['selected_attempt_id'])).scalar_one()==1
        (ctx['history']/identifier).rename(ctx['history']/(identifier+'-preserved'))
        assert client.get('/api/reviews/'+identifier).json()==detail
        assert client.get('/api/reviews/'+identifier+'/image').status_code==200
        assert client.get('/api/reviews/'+identifier+'/original').content==image()
        assert client.get('/api/health').json()['persistence']=='postgres'
    assert (ctx['preserved']/'result.json').read_bytes()==old
    with client_for(ctx) as (client,store):
        assert client.get('/api/reviews/'+identifier).json()==detail
        assert client.get('/api/reviews/'+identifier+'/image').status_code==200


def test_ocr_failure_stays_failed_in_db_without_success_results(context):
    def fail(path):raise RuntimeError('synthetic failure, not a real model')
    with client_for(context,fail) as (client,store):
        response=submit(client);assert response.status_code==202
        identifier=response.json()['id']
        detail=client.get('/api/reviews/'+identifier).json()
        assert detail['status']=='failed' and detail['results']==[]
        with context['db'].engine.connect() as c:
            run=one(c,'review_runs','run_id',UUID(identifier))
            assert run['execution_status']=='failed' and run['overall_verdict'] is None


def test_database_read_failure_never_falls_back_to_success_json(context,monkeypatch):
    with client_for(context) as (client,store):
        response=submit(client);identifier=response.json()['id']
        assert json.loads((context['history']/identifier/'result.json').read_text())['status']=='completed'
        def unavailable(*a):raise PersistenceError('DB_UNAVAILABLE','synthetic DB outage')
        monkeypatch.setattr(store,'read',unavailable)
        assert client.get('/api/reviews/'+identifier).status_code==503


def test_completion_commit_failure_is_not_reported_as_completed(context,monkeypatch):
    def rollback(*a):raise PersistenceError('DB_FAILED','synthetic rollback',outcome='rolled_back')
    monkeypatch.setattr(CompletedWebExecutor,'persist',rollback)
    with client_for(context) as (client,store):
        response=submit(client);identifier=response.json()['id']
        assert client.get('/api/reviews/'+identifier).json()['status']=='failed'
        assert json.loads((context['history']/identifier/'result.json').read_text())['status']=='failed'


def test_session_and_csrf_are_required_for_new_database_records(context):
    with client_for(context) as (client,store):
        assert client.post('/api/reviews',files={'image':('x.png',image(),'image/png')}).status_code==403
        response=submit(client);identifier=response.json()['id']
        client.cookies.clear()
        assert client.get('/api/reviews/'+identifier).status_code==401
        assert client.get('/api/reviews/'+identifier+'/image').status_code==401


def test_restart_closes_a_committed_queued_run_without_rewriting_json(context,monkeypatch):
    from starlette.background import BackgroundTasks
    old=(context['preserved']/'result.json').read_bytes()
    with client_for(context) as (client,store):
        # Simulate process exit after committed admission, before the task starts.
        monkeypatch.setattr(BackgroundTasks,'add_task',lambda *a,**kw:None)
        response=submit(client);identifier=response.json()['id']
        assert client.get('/api/reviews/'+identifier).json()['status']=='queued'
        snapshot=(context['history']/identifier/'result.json').read_bytes()
    with client_for(context) as (client,store):
        assert client.get('/api/reviews/'+identifier).json()['status']=='failed'
    assert (context['history']/identifier/'result.json').read_bytes()==snapshot
    assert (context['preserved']/'result.json').read_bytes()==old
