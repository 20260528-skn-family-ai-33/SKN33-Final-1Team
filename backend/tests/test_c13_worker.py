"""C13 offline profile, description, shutdown and heartbeat authority contracts."""
from pathlib import Path
import time
import tempfile
import pytest
from backend.app.config import ConfigurationError
from backend.app.worker import load_worker_settings,AttemptLease,main
from backend.app.execution.worker_config import QueuedPipeline,write_config
from backend.app.execution.pipeline import code_revision
from backend.tests.auth_fixtures import auth_environment
from backend.tests.c10_fixtures import SyntheticPipeline
from backend.app.security.errors import AuthError


def environment(**extra):
    values=dict(PERSISTENCE_MODE='postgres',DB_HOST='127.0.0.1',DB_PORT='5432',POSTGRES_DB='synthetic_test',
        DB_APP_USER='creative_checker_app',DB_APP_PASSWORD='synthetic-app-secret',APP_ENV='test',
        STORAGE_ROOT=str(Path(tempfile.gettempdir()).resolve()/'cc-test-only'),
        REVIEW_EXECUTOR='postgres_worker',REVIEW_PIPELINE='paddle-only')
    values.update(extra)
    return values


@pytest.mark.parametrize('key,value',[
 ('REVIEW_EXECUTOR','background_tasks'),('REVIEW_PIPELINE','disabled'),('RUN_TARGET','runpod'),
 ('STORAGE_ROOT','relative'),('STORAGE_ROOT','/tmp/../etc'),('LOCAL_ADMIN_LOGIN','1'),
 ('DB_OWNER_PASSWORD','private'),('POSTGRES_PASSWORD','private'),('PERSISTENCE_MODE','legacy_json')])
def test_worker_rejects_implicit_or_privileged_profiles(key,value):
    values=environment();values[key]=value
    with pytest.raises(ConfigurationError):load_worker_settings(values)


def test_minimal_worker_needs_no_browser_or_session_secrets(tmp_path):
    root=tmp_path/'worker-files'
    settings=load_worker_settings(environment(STORAGE_ROOT=str(root)))
    assert settings.persistence_mode=='postgres' and settings.storage_root==root
    assert 'synthetic-app-secret' not in repr(settings)


def manifest():
    config=SyntheticPipeline().describe();config.update(pipeline='paddle_full_v1',model_revision={'detection':{},'recognition':{}},code_revision=code_revision())
    return config


def test_description_is_bounded_frozen_and_never_overwritten(tmp_path,posix_storage):
    path=tmp_path/'config.json';config=manifest();write_config(path,config);pipeline=QueuedPipeline(path)
    assert pipeline.describe()==config
    value=pipeline.describe();value['pipeline']='changed';assert pipeline.describe()==config
    with pytest.raises(FileExistsError):write_config(path,config)
    assert path.stat().st_mode&0o777==0o600


@pytest.mark.parametrize('change',('target','revision','device','scale','duplicate','oversize','symlink'))
def test_web_rejects_invalid_worker_description(tmp_path,change,posix_storage):
    path=tmp_path/'config.json';config=manifest()
    if change=='target':config['execution_target']='runpod'
    if change=='revision':config['code_revision']='old'
    if change=='device':config['device_policy']=['cuda']
    if change=='scale':config['preprocessing']['ocr_scale']=1
    write_config(path,config)
    if change=='duplicate':path.write_text('{"contract_version":"a","contract_version":"b"}')
    if change=='oversize':path.write_bytes(b' '*65537)
    if change=='symlink':link=tmp_path/'link';link.symlink_to(path);path=link
    with pytest.raises(ConfigurationError):QueuedPipeline(path)


@pytest.mark.parametrize('values',[
 {'REVIEW_EXECUTOR':'anything'},
 {'REVIEW_EXECUTOR':'postgres_worker'},
 {'REVIEW_EXECUTOR':'postgres_worker','STORAGE_ROOT':'/tmp/storage','REVIEW_CONFIG_FILE':'relative'},
 {'REVIEW_EXECUTOR':'postgres_worker','STORAGE_ROOT':'/tmp/storage','REVIEW_CONFIG_FILE':'/tmp/config','REVIEW_PIPELINE':'paddle-only'},
 {'REVIEW_CONFIG_FILE':'/tmp/config'}])
def test_web_rejects_ambiguous_executor_profiles(values,tmp_path):
    from backend.app.v1_config import load_v1_settings
    values={key:str(tmp_path/Path(value).name) if value in ('/tmp/storage','/tmp/config') else value
            for key,value in values.items()}
    with pytest.raises(ConfigurationError):load_v1_settings(auth_environment(**values))


def test_web_worker_admission_preserves_developer_login_without_local_model(tmp_path):
    from backend.app.v1_config import load_v1_settings
    settings=load_v1_settings(auth_environment(REVIEW_EXECUTOR='postgres_worker',STORAGE_ROOT=str(tmp_path/'storage'),REVIEW_CONFIG_FILE=str(tmp_path/'config')))
    assert settings.developer_login and settings.review_pipeline=='disabled' and settings.review_executor=='postgres_worker'


def test_failed_heartbeat_never_reacquires_authority():
    class Queue:
        calls=0
        def heartbeat(self,*_):self.calls+=1;raise RuntimeError('private-driver-detail')
    queue=Queue()
    with AttemptLease(queue,({'run_id':1},2,3),interval=.01) as lease:
        assert lease.lost.wait(1)
        with pytest.raises(AuthError):lease.check()
        time.sleep(.03);assert queue.calls==1


def test_cli_checks_configuration_without_db_or_model(tmp_path,monkeypatch,capsys):
    for key in tuple(__import__('os').environ):
        if key.startswith(('DB_','POSTGRES_','REVIEW_','AUTH_','TEST_DB_')) or key in ('APP_ENV','LOCAL_ADMIN_LOGIN','STORAGE_ROOT','RUN_TARGET'):monkeypatch.delenv(key)
    path=tmp_path/'worker.env';path.write_text('\n'.join(k+'='+v for k,v in environment().items()))
    assert main(['--env-file',str(path),'--check-only'])==0
    assert 'worker_configuration_valid' in capsys.readouterr().out
