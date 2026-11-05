"""Opt-in real Docker API + worker against the disposable M008/AF003 test DB."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest

from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database,csrf
from tests.app.auth_fixtures import auth_environment
from tests.agent_framework.test_pdf_postgres_integration import document_database as initial_pdf_database,runtime
from tests.agent_framework.test_criteria_postgres_integration import document_database

IMAGE=os.environ.get('FRAMEWORK_DOCKER_TEST_IMAGE')
pytestmark=[pytest.mark.db_integration,pytest.mark.skipif(not IMAGE,reason='Explicit FRAMEWORK_DOCKER_TEST_IMAGE is required')]
ROOT=Path(__file__).resolve().parents[2]


def test_container_api_pdf_storage_and_worker_use_existing_db_and_same_linux_volume(runtime,tmp_path):
    r=runtime;token=uuid4().hex;volume='cc-fw-test-'+token;container='cc-fw-api-'+token
    env=tmp_path/'container.env'
    network=os.environ.get('AF_TEST_DOCKER_NETWORK')
    values=auth_environment(r.db)
    if network:values.update(DB_HOST='postgres',DB_PORT='5432')
    env.write_text(''.join(k+'='+str(v)+'\n' for k,v in values.items()));env.chmod(0o600)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    launcher=[sys.executable,str(ROOT/'scripts/framework_desktop.py')]
    common=['--image',IMAGE,'--volume',volume]
    # Linux host-gateway cannot reach a DB port published only on host loopback.
    if network:common+=['--network',network]
    proc=None
    try:
        initialized=subprocess.run(launcher+['init-pdf',*common],capture_output=True,text=True,timeout=30)
        assert initialized.returncode==0,initialized.stderr
        proc=subprocess.Popen(launcher+['api',*common,'--container-name',container,'--env-file',str(env),
            '--config',str(ROOT/'examples/agent_framework/team_config.pdf-mock.json'),'--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        with httpx.Client(base_url='http://localhost:'+str(port),headers={'Origin':'http://localhost:8000'},trust_env=False,timeout=5) as client:
            deadline=time.monotonic()+30
            while True:
                try:
                    if client.get('/api/framework/v1/health').status_code==200:break
                except httpx.TransportError:pass
                if proc.poll() is not None:
                    detail=proc.stderr.read().replace(values['DB_APP_PASSWORD'],'***')
                    pytest.fail('Container API exited before startup:\n'+detail)
                assert time.monotonic()<deadline,'Container API startup deadline exceeded'
                time.sleep(0.2)
            client.cookies.update(dict(r.client.cookies))
            response=client.post('/api/v1/projects',json={'name':'Container PDF project '+token},headers=csrf(client))
            assert response.status_code==201,response.text
            project=response.json();base='/api/framework/v1/projects/'+project['id']
            listing=client.get('/api/v1/projects?limit=100')
            assert listing.status_code==200,listing.text
            assert project['id'] in {item['id'] for item in listing.json()['items']}
            raw=next((ROOT/'docs/poster-baselines/2026-11-03/workflow/client-inputs/EXH-NEW004').glob('*.pdf')).read_bytes()
            response=client.post(base+'/documents',params={'filename':'synthetic development source.pdf'},content=raw,
                headers={**csrf(client),'Content-Type':'application/pdf','Idempotency-Key':str(uuid4())})
            assert response.status_code==201,response.text
            record=response.json()
            response=client.post(base+'/document-versions/'+record['document']['document_version_id']+'/extractions',
                headers={**csrf(client),'Idempotency-Key':str(uuid4())})
            assert response.status_code==202,response.text
            rid=response.json()['context']['run_id']
            worker=subprocess.run(launcher+['worker',*common,'--env-file',str(env),
                '--config',str(ROOT/'examples/agent_framework/team_config.pdf-mock.json'),'--once'],capture_output=True,text=True,timeout=60)
            assert worker.returncode==0,worker.stderr
            saved=client.get('/api/framework/v1/runs/'+rid).json()
            assert saved['execution_status']=='waiting_criteria' and saved['synthetic']
            assert saved['criteria']['extraction']['mode']=='mock'
            assert saved['criteria']['extraction']['document_sha256']==record['document']['sha256']
    finally:
        subprocess.run(['docker','stop',container],capture_output=True,timeout=20)
        if proc is not None:
            try:proc.wait(timeout=15)
            except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=10)
        subprocess.run(['docker','volume','rm',volume],capture_output=True,timeout=20)
