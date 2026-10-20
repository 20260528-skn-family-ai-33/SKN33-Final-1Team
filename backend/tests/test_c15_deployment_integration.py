"""Opt-in actual Compose/Linux/TLS/DB/browser plus isolated backup/restore cutover rehearsal.

The ONLY override mounts a test-only synthetic worker. The production web,
proxy, volumes, permissions and queue worker loop use the deployment bundle.
This is not actual Paddle inference or approval to publish a production service.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import json,os,ssl,subprocess,time
import httpx,pytest
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user
from backend.tests.test_c14_backup_integration import fresh_database
from backend.tests.auth_fixtures import PASSWORD
from backend.tests.c12_helpers import ROOT,browser_flow,artifact
from backend.tests.c14_helpers import owner_values,private_env
from backend.tests.c15_helpers import production_values,worker_values,RehearsalPipeline
from backend.app.execution.worker_config import write_config
from backend.app.management.backup import Backups,PgTools,snapshot
from backend.app.management.release import check
from backend.app.management.io import Tree
from backend.app.storage.local import LocalStorage
from backend.app.persistence.transaction import unit_of_work
from backend.migrations.configuration import load_migration_settings

pytestmark=[pytest.mark.db_integration,pytest.mark.parametrize('auth_database',[False],indirect=True)]


def command(args,*,check=True,timeout=60):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    assert not check or result.returncode==0,'Owned C15 operation failed: '+result.stderr[-1800:]
    return result.stdout.strip()


class Bundle:
    def __init__(self,db,tmp):
        assert os.environ.get('C15_RUN_DEPLOYMENT')=='1' and os.environ.get('C15_API_IMAGE'),'Explicit C15 deployment/image opt-in required.'
        self.db=db;self.tmp=tmp.resolve();self.tmp.mkdir();self.tag='cc-c15-'+uuid4().hex[:12]
        self.image=os.environ['C15_API_IMAGE'];self.helper=self.tag+'-copy';self.started=False
        tls=self.tmp/'tls';tls.mkdir();self.cert=tls/'cert.pem';self.models=self.tmp/'models';self.models.mkdir()
        command(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost',
            '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1','-keyout',str(tls/'key.pem'),'-out',str(self.cert)])
        self.config=self.tmp/'worker.json';write_config(self.config,RehearsalPipeline().describe());self.config.chmod(0o644)
        self.webfile=private_env(self.tmp/'web.env',{});self.workerfile=private_env(self.tmp/'worker.env',{})
        # Ephemeral subnet is explicitly test-owned; Docker rejects any collision.
        octet=100+int(uuid4().hex[:2],16)%100;self.proxy_ip=f'172.29.{octet}.10'
        mapping=dict(CC_API_IMAGE=self.image,CC_WORKER_IMAGE=self.image,CC_WEB_ENV_FILE=str(self.webfile),CC_WORKER_ENV_FILE=str(self.workerfile),
            CC_FRONTEND_DIST=str(ROOT/'frontend/dist-v1'),CC_WORKER_CONFIG=str(self.config),CC_MODELS_DIR=str(self.models),CC_TLS_DIR=str(tls),
            CC_SERVER_NAME='localhost',CC_HTTPS_PORT='0',CC_BIND_IP='127.0.0.1',CC_SUBNET=f'172.29.{octet}.0/24',CC_PROXY_IP=self.proxy_ip)
        self.selection=private_env(self.tmp/'deployment.env',mapping)
        self.override=self.tmp/'test-only.override.json';self.override.write_text(json.dumps({'services':{'worker':{
            'command':['python','-m','backend.tests.c15_worker_process','--env-file','/opt/creative/app.env'],
            'environment':{'APP_ENV':'test'},'volumes':[str(ROOT/'backend/tests')+':/opt/creative/backend/tests:ro']}}}))
        self.prefix=['docker','compose','--env-file',str(self.selection),'-p',self.tag,'-f',str(ROOT/'deploy/compose.v1.yaml'),'-f',str(self.override)]
    def compose(self,*args,**kwargs):return command([*self.prefix,*args],**kwargs)
    def prepare(self,*,database_name=None,keys=None):
        self.started=True
        self.compose('up','-d','--no-deps','proxy')
        port=self.compose('port','proxy','443').rsplit(':',1)[1];self.url='https://localhost:'+port
        self.web=production_values(self.db,Path('/data/storage'),Path('/run/worker-config.json'))
        self.web.update(DB_HOST=os.environ.get('C12_CONTAINER_DB_HOST','host.docker.internal'),AUTH_ALLOWED_ORIGINS=self.url,FRONTEND_DIST='/web',TRUSTED_PROXY_IPS=self.proxy_ip)
        if database_name:self.web['POSTGRES_DB']=database_name
        if keys:self.web.update(keys)
        literal={'C15_ENV_LITERAL_PROBE':"literal-$UNSET-$$-'quoted'-\"double\"-#end"}
        self.webfile.write_text('\n'.join(k+'='+v for k,v in dict(self.web,**literal).items())+'\n')
        self.workerfile.write_text('\n'.join(k+'='+v for k,v in dict(worker_values(self.web),**literal).items())+'\n')
        config=json.loads(self.compose('config','--format','json'));self.volume=config['volumes']['data']['name']
        assert not config['services']['api'].get('ports') and not config['services']['worker'].get('ports')
        assert config['services']['api']['read_only'] and config['services']['worker']['read_only']
        return self
    def initialize(self):self.compose('run','--rm','--no-deps','api','python','-m','backend.app.storage','--root','/data/storage','init')
    def start(self):
        self.compose('up','-d','api','worker')
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            try:
                response=httpx.get(self.url+'/api/v1/health',verify=ssl.create_default_context(cafile=str(self.cert)),trust_env=False,timeout=1)
                if response.status_code==200:return
            except httpx.HTTPError:pass
            time.sleep(.1)
        raise AssertionError('C15 API readiness timeout; inspect private owned logs')
    def stop(self):self.compose('stop','-t','180','worker','api','proxy',timeout=60)
    def copy(self,path,*,into=False):
        code="import pathlib,time;pathlib.Path('/data/storage').mkdir(mode=0o700,exist_ok=True);time.sleep(180)"
        command(['docker','run','-d','--name',self.helper,'--mount','type=volume,source='+self.volume+',target=/data',self.image,'python','-c',code])
        try:
            if into:
                # New test-owned volume only, never a source or existing project volume.
                command(['docker','cp',str(path)+'/.',self.helper+':/data/storage'])
                command(['docker','exec','--user','0',self.helper,'chown','-R','10001:10001','/data/storage'])
            else:command(['docker','cp',self.helper+':/data/storage',str(path)])
        finally:command(['docker','rm','-f',self.helper],check=False)
    def close(self):
        if self.started:self.compose('down','--volumes','--remove-orphans',timeout=60)
        self.webfile.unlink(missing_ok=True);self.workerfile.unlink(missing_ok=True);self.selection.unlink(missing_ok=True)


@contextmanager
def bundle(db,tmp):
    value=Bundle(db,tmp)
    try:yield value
    finally:value.close()


def test_linux_compose_https_queue_pre_cutover_backup_restore_and_single_writer(auth_database,tmp_path):
    db,owner=auth_database[:2];user=email_user(auth_database);settings=load_migration_settings(owner_values(db))
    tools=PgTools(settings,owner_values(db));state=tmp_path/'source-browser-state.json'
    with bundle(db,tmp_path/'source') as source:
        source.prepare();source.initialize();source.start()
        flow=browser_flow(dict(url=source.url,https=True,email=user['email'],password=PASSWORD,image=str(ROOT/'frontend/tests/v1/poster.png'),stateFile=str(state)),timeout=120)
        assert flow['secure_cookie_verified'] and flow['model_config']['model_name']=='SYNTHETIC_REHEARSAL_NOT_A_MODEL'
        for service in ('api','worker'):
            assert source.compose('exec','-T',service,'id','-u')=='10001'
        probe="import os;assert not any(os.environ.get(k) for k in ('DB_OWNER_PASSWORD','POSTGRES_PASSWORD'));assert os.environ['C15_ENV_LITERAL_PROBE']==\"literal-$UNSET-$$-'quoted'-\\\"double\\\"-#end\";\ntry:open('/opt/creative/forbidden','w');raise AssertionError('writable root')\nexcept OSError:pass"
        for service in ('api','worker'):source.compose('exec','-T',service,'python','-c',probe)
        context=ssl.create_default_context(cafile=str(source.cert))
        with httpx.Client(base_url=source.url,verify=context,trust_env=False) as client:
            assert client.post('/api/reviews',content=b'bad multipart').status_code==410
            assert client.get('/api/reviews/'+flow['run_id']).status_code==404
            for path in ('/.env','/backend/app/main.py','/storage/private'):assert client.get(path).status_code==404
            health=client.get('/api/v1/health').json();assert health['checks']['worker']['state']=='unknown'
        source.stop();snapshot_root=tmp_path.resolve()/'linux-source-snapshot';source.copy(snapshot_root)
        web=production_values(db,snapshot_root,source.config);web.update(AUTH_ALLOWED_ORIGINS=source.url,TRUSTED_PROXY_IPS=source.proxy_ip)
        storage=LocalStorage(snapshot_root);backup=tmp_path.resolve()/'pre-cutover-backup'
        try:manifest=Backups(owner,settings,tools).capture(storage,backup,web,drain_seconds=0)
        finally:storage.close()
        with unit_of_work(owner) as c:original=snapshot(c)
        with fresh_database(SimpleNamespace(db=db)) as (name,target_settings,target_owner,target_app):
            restored_root=tmp_path.resolve()/'restored';keys=dict(CSRF_SECRET='new-c15-restore-csrf-'+uuid4().hex,CURSOR_SECRET='new-c15-restore-cursor-'+uuid4().hex)
            target_web=dict(web,POSTGRES_DB=name,STORAGE_ROOT=str(restored_root),**keys)
            manager=Backups(target_owner,target_settings,PgTools(target_settings,owner_values(db,name)))
            restored=manager.restore(backup,restored_root,target_web)
            storage=LocalStorage(restored_root)
            try:assert manager.release(storage,target_web)['state']=='released'
            finally:storage.close()
            storage=LocalStorage(snapshot_root)
            try:preflight=check(owner,settings,storage,backup,web,worker_values(web))
            finally:storage.close()
            assert preflight['state']=='preflight_passed' and not preflight['worker_runtime_verified']
            with bundle(db,tmp_path/'target') as target:
                target.prepare(database_name=name,keys=keys);target.copy(restored_root,into=True);target.start()
                cookies={v['name']:v['value'] for v in json.loads(state.read_text())['cookies']}
                with httpx.Client(base_url=target.url,verify=ssl.create_default_context(cafile=str(target.cert)),trust_env=False,cookies=cookies) as client:
                    assert not client.get('/api/v1/auth/me').json()['authenticated']
                new_state=tmp_path/'target-browser-state.json'
                new=browser_flow(dict(url=target.url,https=True,email=user['email'],password=PASSWORD,image=str(ROOT/'frontend/tests/v1/poster.png'),stateFile=str(new_state)),timeout=120)
                target.compose('restart','api','worker');target.start()
                resumed=browser_flow(dict(url=target.url,https=True,resume=True,stateFile=str(new_state),runPath=new['runPath']))
                assert resumed['session_survived_restart']
                with httpx.Client(base_url=target.url,verify=ssl.create_default_context(cafile=str(target.cert)),trust_env=False) as client:
                    csrf=client.get('/api/v1/auth/csrf').json()['csrf_token']
                    assert client.post('/api/v1/auth/login',json={'email':user['email'],'password':PASSWORD},headers={'Origin':target.url,'X-CSRFToken':csrf}).status_code==200
                    detail=client.get('/api/v1/reviews/'+flow['run_id']).json();assert detail['results_complete']
                    assert client.get(detail['image']['url']).status_code==200 and client.get('/api/v1/reviews/'+flow['run_id']+'/export').status_code==200
                    assert client.post('/api/reviews').status_code==410
                target.stop()
        with unit_of_work(owner) as c:assert snapshot(c)==original
        artifact(tmp_path/'c15-linux-rehearsal.json',dict(scope='local_linux_compose_rehearsal_only',
            synthetic_worker=True,actual_paddle_inference=False,remote_paid_calls=0,https_cookie_verified=True,
            api_worker_uid=10001,read_only_containers=True,api_worker_public_ports=False,legacy_write_status=410,legacy_read_status=404,
            original_source_database_preserved=True,preflight=preflight,restored_schema=restored['checks']['schema'],
            sessions_revoked=restored['sessions_revoked'],fresh_keys=True,old_cookie_rejected=True,
            restored_image_export=True,new_review_completed=True,restart_read=True,
            capture_seconds=manifest['capture_seconds'],restore_seconds=restored['restore_seconds'],
            source_flow=flow,restored_flow=new,production_cutover_performed=False,rpo_rto_sla_verified=False))
        for service in ('api','worker'):
            logs=source.compose('logs','--no-color',service)
            for secret in (db.settings.database.password.reveal(),PASSWORD,source.web['CSRF_SECRET']):assert secret not in logs


def test_linux_production_api_and_real_worker_refuse_active_developer(auth_database,tmp_path):
    db,owner,manager=auth_database[:3]
    with unit_of_work(owner) as c:c.exec_driver_sql("INSERT INTO public.users(user_id,account_kind,display_name) VALUES(%s,'developer','blocked developer fixture')",(uuid4(),))
    with bundle(db,tmp_path/'blocked') as target:
        target.prepare();target.initialize()
        result=subprocess.run([*target.prefix,'run','--rm','--no-deps','api','python','-m','scripts.run_v1','--env-file','/opt/creative/app.env','--host','0.0.0.0'],capture_output=True,text=True,timeout=30)
        assert result.returncode!=0
        # Use the real production CLI without the synthetic worker override.
        result=subprocess.run([*target.prefix,'run','--rm','--no-deps','-e','APP_ENV=production','worker','python','-m','backend.app.worker','--env-file','/opt/creative/app.env','--once'],capture_output=True,text=True,timeout=30)
        assert result.returncode!=0 and 'worker_startup_or_execution_rejected' in result.stdout
        for value in (db.settings.database.password.reveal(),target.web['CSRF_SECRET']):assert value not in result.stdout+result.stderr
