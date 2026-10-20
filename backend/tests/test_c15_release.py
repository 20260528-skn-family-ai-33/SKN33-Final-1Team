"""Retirement boundary and deployment profile errors without DB/network access."""
from pathlib import Path
from uuid import uuid4
import json,pytest
from fastapi.testclient import TestClient
from backend.app.config import ConfigurationError
from backend.app.management.io import OpsError
from backend.app.management.release import profiles
from backend.tests.c15_helpers import production_values,worker_values


def values(tmp_path):
    return production_values(None,tmp_path/'files',tmp_path/'config.json')


@pytest.mark.parametrize('change',('owner','developer','http','wildcard_proxy','background','worker_secret','worker_db','worker_root','worker_password'))
def test_invalid_deployment_profiles_are_rejected(tmp_path,change):
    web=values(tmp_path);job=worker_values(web)
    if change=='owner':web['DB_OWNER_PASSWORD']='never-pass-owner'
    if change=='developer':web['LOCAL_ADMIN_LOGIN']='1'
    if change=='http':web['AUTH_ALLOWED_ORIGINS']='http://localhost:8443'
    if change=='wildcard_proxy':web['TRUSTED_PROXY_IPS']='*'
    if change=='background':web.update(REVIEW_EXECUTOR='background_tasks',REVIEW_CONFIG_FILE='')
    if change=='worker_secret':job['CSRF_SECRET']=web['CSRF_SECRET']
    if change=='worker_db':job['POSTGRES_DB']='other'
    if change=='worker_root':job['STORAGE_ROOT']=str(tmp_path/'other')
    if change=='worker_password':job['DB_APP_PASSWORD']='other'
    with pytest.raises((OpsError,ConfigurationError)):profiles(web,job)


def test_matching_linux_target_profiles_can_be_inspected_without_starting_a_worker(tmp_path):
    web=values(tmp_path);app,job=profiles(web,worker_values(web))
    assert app.secure and job.environment=='production' and app.storage_root==job.storage_root


@pytest.mark.parametrize('path',('/api/reviews','/api/reviews/','/api/reviews/'+str(uuid4())+'/retry'))
def test_v1_rejects_old_writes_before_body_parsing_csrf_or_database_access(tmp_path,path):
    from backend.app.main import create_app
    from backend.app.v1_config import load_v1_settings
    web=values(tmp_path)
    client=TestClient(create_app(settings=load_v1_settings(web)),base_url='https://localhost:8443')
    response=client.post(path,content=b'not multipart',headers={'Content-Type':'multipart/form-data'})
    assert response.status_code==410 and response.json()['error']['code']=='LEGACY_WRITE_DISABLED'
    assert response.headers['Cache-Control']=='no-store' and response.headers['Strict-Transport-Security']
    assert client.get(path).status_code==404


def test_explicit_legacy_read_mode_preserves_running_json_and_development_login(tmp_path,monkeypatch):
    from backend.app.api import create_app
    root=tmp_path/'legacy';target=root/str(uuid4());target.mkdir(parents=True)
    raw=b'{"status":"running","source_only":"unchanged"}';(target/'result.json').write_bytes(raw)
    monkeypatch.setenv('LEGACY_READ_ONLY','1');monkeypatch.setenv('APP_ENV','test');monkeypatch.setenv('LOCAL_ADMIN_LOGIN','1')
    with TestClient(create_app(review_root=root),base_url='http://localhost:8000',client=('127.0.0.1',1)) as client:
        assert client.get('/api/reviews/'+target.name).json()['status']=='running'
        assert client.post('/api/reviews',content=b'invalid').status_code==410
        token=client.get('/api/v1/auth/csrf').json()['csrf_token']
        assert client.post('/api/v1/auth/login',json={'username':'admin','password':'admin'},headers={'X-CSRFToken':token,'Origin':'http://localhost:8000'}).status_code==200
    assert (target/'result.json').read_bytes()==raw and sorted(p.name for p in target.iterdir())==['result.json']
    with TestClient(create_app(review_root=root),base_url='http://localhost:8000',client=('198.51.100.1',1)) as remote:
        assert remote.get('/api/reviews/'+target.name,headers={'X-Forwarded-For':'127.0.0.1'}).status_code==404


@pytest.mark.parametrize('flag,environment',(('yes','test'),('1','production')))
def test_legacy_read_mode_cannot_be_enabled_for_production(tmp_path,monkeypatch,flag,environment):
    from backend.app.api import create_app
    monkeypatch.setenv('LEGACY_READ_ONLY',flag);monkeypatch.setenv('APP_ENV',environment)
    with pytest.raises(ValueError):create_app(review_root=tmp_path)
