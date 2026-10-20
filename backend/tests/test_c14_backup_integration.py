"""Actual PostgreSQL 17 custom dump -> NEW template0 DB/root -> security/API verification."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID,uuid4
from pathlib import Path
import json,os,time
import subprocess,sys
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import select,update,func
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user,csrf
from backend.tests.test_c10_reviews_integration import setup,submit
from backend.tests.test_c09_business_integration import project
from backend.tests.c14_helpers import api,source,owner_values,private_env
from backend.tests.c12_helpers import clean_environment
from backend.tests.auth_fixtures import auth_environment
from backend.app.config import load_settings
from backend.app.main import create_app
from backend.app.storage.local import LocalStorage,StorageError
from backend.app.management.backup import Backups,PgTools,snapshot
from backend.app.management.imports import Imports
from backend.app.management.maintenance import offline,frozen_database
from backend.app.management.io import OpsError
from backend.app.execution.queue import WorkerQueue
from backend.app.worker import AttemptLease
from backend.app.services.review_results import ReviewExecutor
from backend.app.services.business import table,one
from backend.app.persistence.transaction import unit_of_work
from backend.app.persistence.database import create_app_engine,PersistenceError
from backend.migrations.configuration import create_migration_engine,load_migration_settings
from fastapi.testclient import TestClient

pytestmark=pytest.mark.db_integration


@pytest.fixture(scope='module')
def env(auth_database,tmp_path_factory):
    base=tmp_path_factory.mktemp('c14-backup').resolve();root=base/'files';LocalStorage.initialize(root)
    settings=replace(auth_database[3],storage_root=root,review_executor='postgres_worker')
    current=SimpleNamespace(db=auth_database[0],owner=auth_database[1],manager=auth_database[2],root=root,settings=settings,user=email_user(auth_database))
    with api(current) as client:
        body,_=setup(client);receipt=submit(client,body);assert receipt.status_code==202
        queue=WorkerQueue(current.db.engine,client.app.state.storage,client.pipeline);claim=queue.claim_next()
        with AttemptLease(queue,claim,interval=.03) as authority:ReviewExecutor(queue,authority).execute_claimed(claim)
        current.run_id=receipt.json()['id'];assert client.get('/api/v1/reviews/'+current.run_id).json()['status']=='completed'
        current.cookies=dict(client.cookies);current.project_id=UUID(body['project_id'])
        legacy=source(base/'legacy');current.legacy=legacy
        imports=Imports(current.owner,client.app.state.storage)
        plan=imports.dry_run(legacy,base,project_id=current.project_id,actor_id=UUID(current.user['id']),namespace='backup-fixture/v1',display_name='수입 복원용')
        assert imports.apply(plan['operation_id'],legacy,base,actor_id=UUID(current.user['id']))['state']=='completed'
        current.imported_run=plan['resources']['run_id']
    values=owner_values(current.db);current.owner_settings=load_migration_settings(values);current.tools=PgTools(current.owner_settings,values)
    current.app_values=auth_environment(current.db,STORAGE_ROOT=str(root),REVIEW_EXECUTOR='postgres_worker',REVIEW_CONFIG_FILE=str(base/'synthetic-not-loaded.json'))
    return current


@contextmanager
def fresh_database(env):
    values=env.db.environment;name='creative_checker_test_'+uuid4().hex
    admin=psycopg.connect(host=values['TEST_DB_HOST'],port=values['TEST_DB_PORT'],dbname='postgres',user=values['TEST_DB_ADMIN_USER'],password=values['TEST_DB_ADMIN_PASSWORD'],autocommit=True)
    owner=None;app=None
    try:
        admin.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(sql.Identifier(name),sql.Identifier(values['TEST_DB_OWNER_USER'])))
        settings=load_migration_settings(owner_values(env.db,name));owner=create_migration_engine(settings)
        app=create_app_engine(load_settings(dict(PERSISTENCE_MODE='postgres',DB_HOST=values['TEST_DB_HOST'],DB_PORT=values['TEST_DB_PORT'],POSTGRES_DB=name,DB_APP_USER=values['TEST_DB_APP_USER'],DB_APP_PASSWORD=values['TEST_DB_APP_PASSWORD'])))
        yield name,settings,owner,app
    finally:
        if app:app.dispose()
        if owner:owner.dispose()
        admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)));admin.close()


def capture(env,path,**kw):
    storage=LocalStorage(env.root)
    try:return Backups(env.owner,env.owner_settings,env.tools).capture(storage,path,env.app_values,drain_seconds=0,legacy_source=env.legacy,**kw)
    finally:storage.close()


def test_actual_dump_restore_new_target_full_graph_sessions_keys_and_restart(env,tmp_path,request):
    backup=tmp_path.resolve()/'backup';manifest=capture(env,backup)
    assert manifest['state']=='complete' and not manifest['restore_ready'] and (backup/'database.dump').stat().st_size>0
    with unit_of_work(env.owner) as c:original=snapshot(c)
    with fresh_database(env) as (name,settings,owner,app):
        root=tmp_path.resolve()/'restored';values=owner_values(env.db,name);tools=PgTools(settings,values);manager=Backups(owner,settings,tools)
        config=dict(env.app_values,POSTGRES_DB=name,STORAGE_ROOT=str(root),CSRF_SECRET='fresh-restore-csrf-'+uuid4().hex,CURSOR_SECRET='fresh-restore-cursor-'+uuid4().hex)
        report=manager.restore(backup,root,config)
        assert report['state']=='validated_held' and report['restore_ready'] and report['sessions_revoked']>0
        assert report['checks']['service_manifests_checked']>=1 and report['checks']['import_manifests_checked']>=1
        held=LocalStorage(root)
        try:
            with pytest.raises(StorageError,match='RESTORE_VALIDATION_REQUIRED'):held.start(shared=True)
        finally:held.close()
        with unit_of_work(owner) as c:
            assert not c.exec_driver_sql('SELECT EXISTS(SELECT 1 FROM public.user_sessions WHERE revoked_at IS NULL)').scalar_one()
            assert one(c,'review_runs','run_id',UUID(env.run_id))['execution_status']=='completed'
            assert one(c,'review_runs','run_id',UUID(env.imported_run))['execution_status']=='imported'
        storage=LocalStorage(root)
        try:assert manager.release(storage,config)['state']=='released'
        finally:storage.close()
        target_settings=replace(env.settings,persistence=load_settings(dict(PERSISTENCE_MODE='postgres',DB_HOST=values['DB_HOST'],DB_PORT=values['DB_PORT'],POSTGRES_DB=name,DB_APP_USER=values['DB_APP_USER'],DB_APP_PASSWORD=env.db.environment['TEST_DB_APP_PASSWORD'])),storage_root=root,
            csrf_secret=__import__('backend.app.config',fromlist=['Secret']).Secret(config['CSRF_SECRET']),cursor_secret=__import__('backend.app.config',fromlist=['Secret']).Secret(config['CURSOR_SECRET']))
        from backend.tests.c10_fixtures import SyntheticPipeline
        with TestClient(create_app(settings=target_settings,engine=app,review_adapter=SyntheticPipeline()),base_url='http://localhost:8000',client=('127.0.0.1',12)) as client:
            client.headers['Origin']='http://localhost:8000';client.cookies.update(env.cookies)
            assert not client.get('/api/v1/auth/me').json()['authenticated']
        # New login and fresh writes after restore, then another app restart.
        with api(env,settings=target_settings,engine=app) as client:
            detail=client.get('/api/v1/reviews/'+env.run_id).json();assert detail['results_complete']
            assert client.get(detail['image']['url']).status_code==200 and client.get('/api/v1/reviews/'+env.run_id+'/export').status_code==200
            assert client.get('/api/v1/reviews/'+env.imported_run).json()['status']=='imported'
            pid=project(client);assert pid;cookies=dict(client.cookies)
            body,_=setup(client);receipt=submit(client,body);assert receipt.status_code==202
            queue=WorkerQueue(app,client.app.state.storage,client.pipeline);claim=queue.claim_next()
            with AttemptLease(queue,claim,interval=.03) as authority:ReviewExecutor(queue,authority).execute_claimed(claim)
            assert client.get('/api/v1/reviews/'+receipt.json()['id']).json()['status']=='completed'
        with TestClient(create_app(settings=target_settings,engine=app,review_adapter=SyntheticPipeline()),base_url='http://localhost:8000') as client:
            client.headers['Origin']='http://localhost:8000';client.cookies.update(cookies)
            assert client.get('/api/v1/projects/'+pid).status_code==200
        (tmp_path/'restore-drill.json').write_text(json.dumps(dict(backup_capture_seconds=manifest['capture_seconds'],restore_seconds=report['restore_seconds'],source_tables=len(manifest['tables']),
            registered_files=len(manifest['registered_files']),source_state_preserved=True,sessions_revoked=report['sessions_revoked'],keys_rotated=True,checks=report['checks'],
            restored_old_cookie_rejected=True,new_login_and_write=True,new_synthetic_review_completed=True,restart_read=True,remote_calls=0,rpo_sla_verified=False,rto_sla_verified=False),ensure_ascii=False,indent=2))
        request.node.user_properties.append(('c14_restore_drill','actual pg_dump/pg_restore PostgreSQL17; fresh template0 DB/root; full rows/hashes/graph/files/privileges; revoke sessions; fresh keys; old cookie rejected; new login/write/restart'))
    with unit_of_work(env.owner) as c:assert snapshot(c)==original
    assert Backups(env.owner,env.owner_settings,env.tools).inspect(backup)['restore_ready']


@pytest.mark.parametrize('component',('dump','file','manifest','missing_complete'))
def test_corrupt_or_partial_backup_is_never_a_restore_candidate(env,tmp_path,component):
    backup=tmp_path.resolve()/'backup';capture(env,backup)
    if component=='dump':(backup/'database.dump').write_bytes(b'corrupt')
    elif component=='manifest':(backup/'manifest.json').write_bytes(b'{}')
    elif component=='missing_complete':(backup/'COMPLETE.json').unlink()
    else:
        data=json.loads((backup/'manifest.json').read_text());(backup/'storage'/data['storage_files'][0]['relative_path']).write_bytes(b'corrupt')
    with pytest.raises((OpsError,KeyError,OSError)):Backups(env.owner,env.owner_settings,env.tools).inspect(backup)


def test_active_service_or_unfinished_inference_does_not_create_a_complete_backup(env,tmp_path):
    with api(env):
        with pytest.raises(OpsError,match='DRAIN_TIMEOUT'):capture(env,tmp_path.resolve()/'busy')
    assert not (tmp_path/'busy/COMPLETE.json').exists()
    with api(env) as client:
        body,_=setup(client);submit(client,body);queue=WorkerQueue(env.db.engine,client.app.state.storage,client.pipeline);claim=queue.claim_next()
    try:
        with pytest.raises(OpsError,match='ACTIVE_WORK_REQUIRES_RECOVERY'):capture(env,tmp_path.resolve()/'active')
        assert not (tmp_path/'active/COMPLETE.json').exists()
    finally:queue.fail(claim[0]['run_id'],claim[1],claim[2],code='SYNTHETIC_TEST_CLEANUP')


def test_owner_table_locks_block_writes_outside_the_web_process(env):
    storage=LocalStorage(env.root)
    try:
        with offline(env.owner,storage,drain_seconds=0),frozen_database(env.owner):
            with pytest.raises(PersistenceError):
                with unit_of_work(env.db.engine) as c:
                    c.exec_driver_sql("SET LOCAL lock_timeout='30ms'")
                    c.execute(update(table('user_sessions')).values(revoked_at=func.clock_timestamp()))
    finally:storage.close()


def test_restore_rejects_old_security_keys_and_populated_target(env,tmp_path):
    backup=tmp_path.resolve()/'backup';capture(env,backup)
    with fresh_database(env) as (name,settings,owner,app):
        manager=Backups(owner,settings,PgTools(settings,owner_values(env.db,name)));root=tmp_path.resolve()/'restore'
        with pytest.raises(OpsError,match='RESTORE_FRESH_SECURITY_KEYS_REQUIRED'):manager.restore(backup,root,dict(env.app_values,POSTGRES_DB=name,STORAGE_ROOT=str(root)))
        with unit_of_work(owner) as c:c.exec_driver_sql('CREATE TABLE public.not_empty(value int)')
        with pytest.raises(OpsError,match='RESTORE_DATABASE_NOT_EMPTY'):manager.restore(backup,root,dict(env.app_values,POSTGRES_DB=name,STORAGE_ROOT=str(root),CSRF_SECRET='fresh-csrf-'+uuid4().hex,CURSOR_SECRET='fresh-cursor-'+uuid4().hex))
        assert not root.exists()


def test_missing_registered_payload_is_degraded_and_not_automatically_restorable(env,tmp_path):
    with unit_of_work(env.owner) as c:
        image=c.execute(select(table('asset_versions'))).mappings().first();row=one(c,'stored_files','file_id',image['display_file_id'])
    path=env.root/row['storage_key'];held=tmp_path/'saved-payload';path.rename(held)
    try:
        backup=tmp_path.resolve()/'degraded';manifest=capture(env,backup)
        assert manifest['state']=='degraded' and not manifest['restore_ready']
        with pytest.raises(OpsError,match='BACKUP_NOT_RESTORE_READY'):Backups(env.owner,env.owner_settings,env.tools).inspect(backup)
    finally:held.rename(path)


def test_interrupted_restore_keeps_fresh_target_held(env,tmp_path):
    backup=tmp_path.resolve()/'backup';capture(env,backup)
    class FailedTools:
        def version(self,*_):return 'pg_restore (PostgreSQL) 17.11'
        def connection(self):return []
        def run(self,*_,**__):raise OpsError('PG_TOOL_FAILED')
    with fresh_database(env) as (name,settings,owner,app):
        root=tmp_path.resolve()/'held';config=dict(env.app_values,POSTGRES_DB=name,STORAGE_ROOT=str(root),CSRF_SECRET='fresh-csrf-'+uuid4().hex,CURSOR_SECRET='fresh-cursor-'+uuid4().hex)
        with pytest.raises(OpsError,match='PG_TOOL_FAILED'):Backups(owner,settings,FailedTools()).restore(backup,root,config)
        assert (root/'restore-hold.json').exists() and not (root/'restore-report.json').exists()
        storage=LocalStorage(root)
        try:
            with pytest.raises(StorageError):storage.start(shared=True)
        finally:storage.close()


def test_real_backup_cli_and_offline_inspection_do_not_expose_credentials(env,tmp_path):
    values=owner_values(env.db);ownerfile=private_env(tmp_path/'owner.env',values);appfile=private_env(tmp_path/'app.env',env.app_values);backup=tmp_path.resolve()/'backup'
    command=[sys.executable,'-m','backend.app.management','--env-file',str(ownerfile)]
    try:
        result=subprocess.run([*command,'backup','--root',str(env.root),'--output',str(backup),'--app-env-file',str(appfile),'--drain-seconds','0'],env=clean_environment(),capture_output=True,text=True,timeout=45)
        assert result.returncode==0,result.stdout
        assert json.loads(result.stdout)['result']['state']=='complete'
        # Inspection must also work while the configured database is unavailable.
        ownerfile.write_text('\n'.join(k+'='+v for k,v in dict(values,DB_PORT='1').items())+'\n')
        inspected=subprocess.run([*command,'inspect-backup','--backup',str(backup)],env=clean_environment(),capture_output=True,text=True,timeout=15)
        assert inspected.returncode==0 and json.loads(inspected.stdout)['result']['state']=='complete'
        for secret in (values['DB_OWNER_PASSWORD'],env.app_values['CSRF_SECRET'],env.app_values['CURSOR_SECRET']):assert secret not in result.stdout+result.stderr+inspected.stdout+inspected.stderr
    finally:ownerfile.unlink();appfile.unlink()
