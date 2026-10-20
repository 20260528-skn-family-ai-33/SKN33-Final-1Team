"""Synthetic sources and isolated owner settings; no production environment fallback."""
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import json,os
from PIL import Image
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.config import read_environment_file
from backend.app.storage.local import LocalStorage
from backend.tests.c10_fixtures import SyntheticPipeline
from backend.tests.test_v1_auth_integration import email_user,login


def source(path,*,partial=False,external=False):
    path.mkdir();raw=BytesIO();Image.new('RGB',(4,3),'white').save(raw,format='PNG');(path/'original.bin').write_bytes(raw.getvalue())
    items=[dict(id='one',text='행사명 표기',type='information',source='원문')]
    if partial:items.append(dict(id='two',text='장소 표기',type='information',source='원문'))
    snapshot=dict(criteria_version=7,project_name='과거 합성 프로젝트',criteria=items)
    result=dict(id=str(uuid4()),status='completed',requester='local-admin',created_at='2020-01-01T00:00:00Z',
                results=[dict(criterion_id='one',status='pass',observed_text='과거 관찰',reason='과거 원문 판정',evidence=[{'bbox_xyxy':[1,2,3,4]}])])
    for name,value in [('snapshot.json',snapshot),('result.json',result)]:
        (path/name).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    if external:(path/'manifest.json').write_text(json.dumps(dict(contract_version='cc-import-source-v1',files=dict(snapshot='snapshot.json',result='result.json',image='original.bin'))))
    return path


@contextmanager
def api(env,*,settings=None,engine=None):
    adapter=SyntheticPipeline()
    with TestClient(create_app(settings=settings or env.settings,engine=engine or env.db.engine,review_adapter=adapter),base_url='http://localhost:8000',client=('127.0.0.1',12)) as client:
        client.headers['Origin']='http://localhost:8000';assert login(client,email=env.user['email']).status_code==200
        client.pipeline=adapter;yield client


def owner_values(db,name=None):
    values=db.environment
    return dict(DB_HOST=values['TEST_DB_HOST'],DB_PORT=values['TEST_DB_PORT'],POSTGRES_DB=name or db.name,
        DB_OWNER_USER=values['TEST_DB_OWNER_USER'],DB_OWNER_PASSWORD=values['TEST_DB_OWNER_PASSWORD'],DB_APP_USER=values['TEST_DB_APP_USER'],
        PG_TOOLS_MODE='docker',PG_TOOLS_IMAGE='postgres:17.11-bookworm',PG_TOOLS_HOST='host.docker.internal',PG_TOOLS_PORT=values['TEST_DB_PORT'])


def private_env(path,values):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write('\n'.join(k+'='+v for k,v in values.items())+'\n')
    return path
