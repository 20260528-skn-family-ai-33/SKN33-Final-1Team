"""Real v1 UI/API/DB, direct and Vite. One explicitly injected committed-response loss."""
from pathlib import Path
import os,socket,sys
from uuid import UUID
import httpx,pytest
from sqlalchemy import select,func
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user
from backend.tests.auth_fixtures import PASSWORD,DEV_PROJECT
from backend.tests.c12_helpers import ROOT,server,process,wait_ready,browser_flow,clean_environment,login,artifact
from backend.app.storage.local import LocalStorage
from backend.app.persistence.schema.criteria import criteria_versions
from backend.app.persistence.schema.reviews import review_runs

pytestmark=pytest.mark.db_integration


@pytest.mark.parametrize('mode',('direct','vite'))
def test_real_browser_full_flow_commit_loss_and_restart(auth_database,tmp_path,mode):
    db,owner,manager,*_=auth_database;user=email_user(auth_database);viewer=email_user(auth_database);outsider=email_user(auth_database)
    root=tmp_path.resolve()/'files';LocalStorage.initialize(root);statefile=tmp_path/'browser-state.json';logpath=tmp_path/'service.log'
    with logpath.open('w+') as log:
        if mode=='vite':
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close();front='http://127.0.0.1:'+str(port)
        else:front=None
        with server(db,root,log,extra_origins=(front,) if front else ()) as (api,values):
            config=dict(url=front or api,email=user['email'],password=PASSWORD,image=str(ROOT/'frontend/tests/v1/poster.png'),stateFile=str(statefile),loseConfirm=True,screenshotDir=str(tmp_path/'screenshots'))
            if front:
                env=clean_environment();env['API_PROXY_TARGET']=api
                with process(['node','node_modules/vite/bin/vite.js','--mode','v1','--host','127.0.0.1','--port',str(port),'--strictPort'],log,env=env,cwd=ROOT/'frontend') as vite:
                    wait_ready(front,vite);result=browser_flow(config)
                    # Actual Vite transport must preserve Host/Origin and must not add forwarded trust.
                    developer=browser_flow({**config,'loseConfirm':False,'developer':True,'screenshotDir':None,'stateFile':None});assert developer['developer_login_verified']
                    with httpx.Client(base_url=front,headers={'Origin':front},trust_env=False) as proxy_client:
                        for forged in ({'Forwarded':'for=203.0.113.1'},{'X-Forwarded-For':'127.0.0.1'},{'X-Forwarded-Proto':'https'},{'X-Forwarded-Prefix':'/'}):
                            assert not proxy_client.get('/api/v1/auth/csrf',headers=forged).json()['developer_login_available']
                        prepared=proxy_client.get('/api/v1/auth/csrf').json()['csrf_token']
                        assert proxy_client.post('/api/v1/auth/login',json={'username':'admin','password':'admin'},headers={'Origin':'https://foreign.invalid','X-CSRFToken':prepared}).status_code==403
                        assert proxy_client.get('/api/v1/health',headers={'Host':'127.0.0.2'}).status_code==404
            else:result=browser_flow(config)
            assert result['status']=='passed' and result['response_loss_injected']
            with owner.connect() as c:
                assert c.execute(select(func.count()).select_from(criteria_versions).where(criteria_versions.c.source_draft_id==UUID(result['draft_id']))).scalar_one()==1
                assert c.execute(select(func.count()).select_from(review_runs).where(review_runs.c.project_id==UUID(result['project_id']))).scalar_one()==1
            manager.set_project_member(UUID(result['project_id']),UUID(viewer['id']),'viewer',True)
            with httpx.Client(base_url=api,headers={'Origin':api},trust_env=False) as client:
                login(client,viewer['email'],PASSWORD);assert client.get('/api/v1/reviews/'+result['run_id']).status_code==200
                token=client.get('/api/v1/auth/csrf').json()['csrf_token'];assert client.post('/api/v1/reviews/'+result['run_id']+'/retry',json={},headers={'X-CSRFToken':token,'Idempotency-Key':'10000000-0000-4000-8000-000000000001'}).status_code==403
            with httpx.Client(base_url=api,headers={'Origin':api},trust_env=False) as client:
                login(client,outsider['email'],PASSWORD);assert client.get('/api/v1/reviews/'+result['run_id']).status_code==404;assert client.get('/api/v1/images/'+result['image_id']+'/content').status_code==404
            with httpx.Client(base_url=api,headers={'Origin':api},trust_env=False) as client:
                assert client.get('/api/v1/reviews/'+result['run_id']).status_code==401
                for path in ('/api/v1/no-such-api','/assets/missing-12345678.js','/.env','/storage/private','/backend/app/main.py'):
                    response=client.get(path);assert response.status_code==404 and 'text/html' not in response.headers['content-type']
                for header in ({'Forwarded':'for=203.0.113.1'},{'X-Forwarded-For':'127.0.0.1'},{'X-Forwarded-Proto':'https'},{'X-Forwarded-Prefix':'/'}):
                    assert not client.get('/api/v1/auth/csrf',headers=header).json()['developer_login_available']
                assert client.get('/api/v1/health',headers={'Host':'foreign.invalid'}).status_code==404
                token=client.get('/api/v1/auth/csrf').json()['csrf_token'];assert client.post('/api/v1/auth/login',json={'email':user['email'],'password':PASSWORD},headers={'Origin':'https://foreign.invalid','X-CSRFToken':token}).status_code==403
        # New process and address; cookie storage is host scoped, so the real DB session must survive.
        with server(db,root,log) as (api,_):
            resumed=browser_flow(dict(url=api,resume=True,stateFile=str(statefile),runPath=result['runPath']));assert resumed['session_survived_restart']
    source=logpath.read_text();assert PASSWORD not in source and db.settings.database.password.reveal() not in source
    statefile.unlink();artifact(tmp_path/'c12-live-result.json',dict(mode=mode,flow=result,restart=resumed,viewer_and_nonmember_verified=True))


@pytest.mark.ocr_external
def test_actual_paddle_browser_path(auth_database,tmp_path):
    assert os.environ.get('C12_RUN_REAL_OCR')=='1','Actual OCR requires explicit C12_RUN_REAL_OCR=1; selected runs must not silently skip.'
    from backend.app.paddle_ocr import paddle_readiness
    assert paddle_readiness(check_import=True)[0]
    user=email_user(auth_database);root=tmp_path.resolve()/'files';LocalStorage.initialize(root)
    with (tmp_path/'paddle.log').open('w+') as log:
        with server(auth_database[0],root,log,pipeline='paddle-only') as (url,_):
            result=browser_flow(dict(url=url,email=user['email'],password=PASSWORD,image=str(ROOT/'data/img/2026_김천_김밥축제.jpg'),requirement='행사명: 김천 김밥축제',screenshotDir=str(tmp_path/'screenshots')),timeout=220)
            assert result['runtime']['pipeline']=='paddle_full_v1' and result['runtime']['device']=='cpu'
            health=httpx.get(url+'/api/v1/health',trust_env=False).json();assert health['checks']['ocr_runtime']['state']=='ready'
    artifact(tmp_path/'c12-real-ocr-result.json',dict(flow=result,health=health,quality_evaluation=False,scope='One existing poster; actual CPU model/path and persistence, not OCR accuracy or SLA.'))
