"""Actual loopback HTTP, post-response work and SIGKILL/restart; synthetic model only."""
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import os,socket,subprocess,sys,time
from uuid import uuid4
import httpx,pytest
from PIL import Image
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user
from backend.tests.auth_fixtures import auth_environment,PASSWORD
from backend.app.storage.local import LocalStorage

pytestmark=pytest.mark.db_integration


@contextmanager
def server(db,root,gate,log):
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);address='http://127.0.0.1:'+str(sock.getsockname()[1])
    values=auth_environment(db,AUTH_ALLOWED_ORIGINS=address,AUTH_ALLOWED_HOSTS='127.0.0.1',STORAGE_ROOT=str(root))
    app_file=root.parent/('app-'+uuid4().hex+'.env');app_file.write_text('\n'.join(k+'='+v for k,v in values.items())+'\n');app_file.chmod(0o600)
    forbidden=('DB_OWNER_PASSWORD','POSTGRES_PASSWORD','DB_OWNER_USER','CC_MIGRATION_ENV_FILE','CC_APP_ENV_FILE')
    environment={k:v for k,v in os.environ.items() if not k.startswith('TEST_DB_') and k not in forbidden and k not in values}
    environment.update(CC_APP_ENV_FILE=str(app_file),CC_C10_TEST_GATE_ROOT=str(gate))
    process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.tests.c10_live_factory:factory','--factory','--fd',str(sock.fileno()),
        '--workers','1','--no-proxy-headers','--no-access-log','--log-level','warning'],env=environment,pass_fds=(sock.fileno(),),stdout=log,stderr=log)
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            assert process.poll() is None,'Synthetic C10 server failed startup'
            try:
                if httpx.get(address+'/api/v1/health',timeout=.3,trust_env=False).status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.03)
        else:raise AssertionError('Synthetic C10 server readiness timeout')
        yield address,process
    finally:
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
        sock.close();app_file.unlink()


def wait_state(browser,run_id,status):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        result=browser.get('/api/v1/reviews/'+run_id).json()
        if result['status']==status and (status!='running' or result['stage']=='ocr'):return result
        time.sleep(.03)
    raise AssertionError('C10 synthetic state transition timeout')


def test_actual_http_background_response_sigkill_restart_and_explicit_retry(auth_database,tmp_path,request):
    root=tmp_path.resolve()/'files';LocalStorage.initialize(root)
    gate=tmp_path.resolve()/'gate';gate.mkdir();(gate/'allow').touch()
    user=email_user(auth_database)
    raw=BytesIO();Image.new('RGB',(4,3),'white').save(raw,format='PNG');key=str(uuid4())
    def headers(browser):return {'X-CSRFToken':browser.get('/api/v1/auth/csrf').json()['csrf_token']}
    with (tmp_path/'live.log').open('w+') as log:
        with server(auth_database[0],root,gate,log) as (address,process):
            with httpx.Client(base_url=address,headers={'Origin':address},trust_env=False,timeout=3) as browser:
                assert browser.post('/api/v1/auth/login',json={'email':user['email'],'password':PASSWORD},headers=headers(browser)).status_code==200
                pid=browser.post('/api/v1/projects',json={'name':'C10 실제 HTTP'},headers=headers(browser)).json()['id'];base='/api/v1/projects/'+pid
                image=browser.post(base+'/images',files={'image':('image.png',raw.getvalue(),'image/png')},headers=headers(browser)).json()
                sid=browser.post(base+'/criteria-sets',json={'name':'기준'},headers=headers(browser)).json()['id']
                draft=browser.post(base+'/criteria-drafts',json=dict(criteria_set_id=sid,image_id=image['id'],raw_requirements='예시 축제',items=[dict(id='1',text='행사명: 예시 축제',type='information',source='원문',selected=True)]),headers=headers(browser)).json()
                version=browser.post(base+'/criteria-versions',json=dict(draft_id=draft['id'],expected_revision='1'),headers={**headers(browser),'Idempotency-Key':str(uuid4())}).json()
                body=dict(project_id=pid,image_id=image['id'],criteria_version_id=version['id'])
                response=browser.post('/api/v1/reviews',json=body,headers={**headers(browser),'Idempotency-Key':key});assert response.status_code==202
                completed=response.json()['id'];first=wait_state(browser,completed,'completed');assert first['results_complete']
                (gate/'allow').unlink()
                response=browser.post('/api/v1/reviews',json=body,headers={**headers(browser),'Idempotency-Key':str(uuid4())});assert response.status_code==202
                interrupted=response.json()['id'];running=wait_state(browser,interrupted,'running');assert running['stage']=='ocr'
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and len((gate/'calls').read_text().splitlines())<2:time.sleep(.01)
                assert len((gate/'calls').read_text().splitlines())==2
                cookies=browser.cookies;process.kill();process.wait(timeout=3)
        with server(auth_database[0],root,gate,log) as (address,process):
            with httpx.Client(base_url=address,cookies=cookies,headers={'Origin':address},trust_env=False,timeout=3) as browser:
                failed=browser.get('/api/v1/reviews/'+interrupted).json();assert failed['status']=='failed' and failed['error']['outcome_unknown']
                assert failed['error']['code']=='RESTART_DURING_EXECUTION'
                assert len((gate/'calls').read_text().splitlines())==2
                old=browser.get('/api/v1/reviews/'+completed).json();assert old['status']=='completed' and old['overall_verdict']==first['overall_verdict']
                replay=browser.post('/api/v1/reviews',json=body,headers={**headers(browser),'Idempotency-Key':key})
                assert replay.status_code==200 and replay.json()['id']==completed
                response=browser.post('/api/v1/reviews/'+interrupted+'/retry',json={},headers={**headers(browser),'Idempotency-Key':str(uuid4())});assert response.status_code==202
                (gate/'allow').touch();retried=wait_state(browser,response.json()['id'],'completed')
                assert retried['retry_of']==interrupted and len((gate/'calls').read_text().splitlines())==3
        log.flush();log.seek(0);source=log.read()
    for private in (PASSWORD,user['email'],auth_database[3].database.password.reveal()):assert private not in source
    request.node.user_properties.append(('c10_live','real HTTP 202 before blocked OCR; SIGKILL; committed output retained; interrupted closed; no automatic re-inference; explicit retry only; synthetic adapter'))
