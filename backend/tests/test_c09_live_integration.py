"""Real loopback HTTP and independent Uvicorn restart with app-only credentials."""
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import httpx
from PIL import Image
import pytest
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user
from backend.tests.auth_fixtures import PASSWORD
import backend.tests.test_v1_live_server_integration as live
from backend.app.storage.local import LocalStorage

pytestmark=pytest.mark.db_integration


def test_real_http_upload_confirm_and_restart_replay(auth_database,tmp_path,monkeypatch,request):
    root=tmp_path.resolve()/'live-files';LocalStorage.initialize(root)
    original=live.auth_environment
    monkeypatch.setattr(live,'auth_environment',lambda *args,**kwargs:dict(original(*args,**kwargs),STORAGE_ROOT=str(root)))
    user=email_user(auth_database);raw=BytesIO();Image.new('RGB',(5,4),'red').save(raw,format='PNG')
    key=str(uuid4())
    def headers(browser): return {'X-CSRFToken':browser.get('/api/v1/auth/csrf').json()['csrf_token']}
    with (tmp_path/'c09-live.log').open('w+') as log:
        with live.server(auth_database,log) as address:
            with httpx.Client(base_url=address,headers={'Origin':address},trust_env=False,timeout=5) as browser:
                response=browser.post('/api/v1/auth/login',json={'email':user['email'],'password':PASSWORD},headers=headers(browser));assert response.status_code==200
                response=browser.post('/api/v1/projects',json={'name':'실제 HTTP 프로젝트'},headers=headers(browser));assert response.status_code==201
                pid=response.json()['id'];base='/api/v1/projects/'+pid
                response=browser.post(base+'/images',files={'image':('poster.png',raw.getvalue(),'image/png')},headers=headers(browser));assert response.status_code==201,response.text
                image=response.json();assert browser.get(image['url']).content.startswith(b'\x89PNG')
                sid=browser.post(base+'/criteria-sets',json={'name':'기준'},headers=headers(browser)).json()['id']
                draft=browser.post(base+'/criteria-drafts',json=dict(criteria_set_id=sid,image_id=image['id'],raw_requirements='안내 문구',items=[dict(id='1',text='안내 문구',type='information',source='원문',selected=True)]),headers=headers(browser)).json()
                body=dict(draft_id=draft['id'],expected_revision='1')
                response=browser.post(base+'/criteria-versions',json=body,headers={**headers(browser),'Idempotency-Key':key});assert response.status_code==201,response.text
                version=response.json();cookies=browser.cookies
        with live.server(auth_database,log) as address:
            with httpx.Client(base_url=address,cookies=cookies,headers={'Origin':address},trust_env=False,timeout=5) as browser:
                assert browser.get(image['url']).status_code==200
                response=browser.post(base+'/criteria-versions',json=body,headers={**headers(browser),'Idempotency-Key':key})
                assert response.status_code==200 and response.json()['id']==version['id']
                assert response.headers['Idempotency-Replayed']=='true'
                assert browser.get('/api/v1/health').json()['checks']['storage']['state']=='ready'
        log.flush();log.seek(0);source=log.read()
    for private in (PASSWORD,user['email'],auth_database[3].database.password.reveal()): assert private not in source
    request.node.user_properties.append(('c09_live', 'real HTTP upload/confirm; process restart; DB session/content/idempotency retained; app-only credentials'))
