"""Actual pre-cutover backup freshness and owner-only CLI. No service switch."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import json,subprocess,sys
import pytest
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database
from backend.tests.test_c14_backup_integration import env,fresh_database
from backend.tests.c14_helpers import private_env
from backend.tests.c12_helpers import clean_environment
from backend.tests.c15_helpers import production_values,worker_values,RehearsalPipeline
from backend.app.execution.worker_config import write_config
from backend.app.management.backup import Backups,snapshot
from backend.app.management.release import check
from backend.app.management.io import OpsError
from backend.app.storage.local import LocalStorage
from backend.app.persistence.transaction import unit_of_work

pytestmark=[pytest.mark.db_integration,pytest.mark.parametrize('auth_database',[False],indirect=True)]


@pytest.fixture(scope='module')
def ready(env,tmp_path_factory):
    base=tmp_path_factory.mktemp('c15-release').resolve();config=base/'worker.json';write_config(config,RehearsalPipeline().describe())
    web=production_values(env.db,env.root,config);worker=worker_values(web)
    storage=LocalStorage(env.root)
    try:Backups(env.owner,env.owner_settings,env.tools).capture(storage,base/'backup',web,drain_seconds=0)
    finally:storage.close()
    with fresh_database(env) as (name,settings,owner,app):
        manager=Backups(owner,settings,__import__('backend.app.management.backup',fromlist=['PgTools']).PgTools(settings,__import__('backend.tests.c14_helpers',fromlist=['owner_values']).owner_values(env.db,name)))
        restored=dict(web,POSTGRES_DB=name,STORAGE_ROOT=str(base/'restored'),CSRF_SECRET='new-c15-csrf-'+uuid4().hex,CURSOR_SECRET='new-c15-cursor-'+uuid4().hex)
        assert manager.restore(base/'backup',base/'restored',restored)['state']=='validated_held'
    return SimpleNamespace(env=env,base=base,web=web,worker=worker,backup=base/'backup')


def run_check(ready):
    e=ready.env;storage=LocalStorage(e.root)
    try:return check(e.owner,e.owner_settings,storage,ready.backup,ready.web,ready.worker)
    finally:storage.close()


def test_fresh_backup_full_restore_proof_and_profiles_pass_without_business_writes(ready):
    with unit_of_work(ready.env.owner) as c:before=snapshot(c)
    result=run_check(ready)
    assert result['state']=='preflight_passed' and not result['worker_runtime_verified'] and not result['production_cutover_performed']
    assert result['restore_drills']==1 and result['checks']['schema']['revision']=='M008'
    with unit_of_work(ready.env.owner) as c:assert snapshot(c)==before


@pytest.mark.parametrize('kind',('database','files'))
def test_preflight_rejects_changes_after_the_validated_backup(ready,kind):
    e=ready.env
    if kind=='files':(e.root/'unreviewed-change').write_bytes(b'new')
    elif kind=='database':
        with unit_of_work(e.owner) as c:c.exec_driver_sql("INSERT INTO c01_probe.records(id,value) VALUES('c15-stale','c15-stale')")
    try:
        with pytest.raises(OpsError,match='RELEASE_BACKUP_(DATABASE|FILES)_STALE'):run_check(ready)
    finally:
        if kind=='files':(e.root/'unreviewed-change').unlink()
        elif kind=='database':
            with unit_of_work(e.owner) as c:c.exec_driver_sql("DELETE FROM c01_probe.records WHERE id='c15-stale'")


def test_actual_restore_proof_is_required_for_an_unrehearsed_set(env,tmp_path):
    config=tmp_path/'config.json';write_config(config,RehearsalPipeline().describe());web=production_values(env.db,env.root,config)
    storage=LocalStorage(env.root)
    try:
        Backups(env.owner,env.owner_settings,env.tools).capture(storage,tmp_path.resolve()/'backup',web,drain_seconds=0)
    finally:storage.close()
    storage=LocalStorage(env.root)
    try:
        with pytest.raises(OpsError,match='RELEASE_ACTUAL_RESTORE_DRILL_REQUIRED'):
            check(env.owner,env.owner_settings,storage,tmp_path.resolve()/'backup',web,worker_values(web))
    finally:storage.close()


def test_real_release_cli_never_emits_credentials_and_never_switches_a_service(ready,tmp_path):
    from backend.tests.c14_helpers import owner_values
    owner=private_env(tmp_path/'owner.env',owner_values(ready.env.db));web=private_env(tmp_path/'web.env',ready.web);worker=private_env(tmp_path/'worker.env',ready.worker)
    try:
        result=subprocess.run([sys.executable,'-m','backend.app.management','--env-file',str(owner),'release-check','--root',str(ready.env.root),
            '--backup',str(ready.backup),'--app-env-file',str(web),'--worker-env-file',str(worker)],env=clean_environment(),capture_output=True,text=True,timeout=20)
        assert result.returncode==0 and json.loads(result.stdout)['result']['state']=='preflight_passed'
        assert not json.loads(result.stdout)['result']['production_cutover_performed']
        for value in (ready.web['DB_APP_PASSWORD'],ready.web['CSRF_SECRET'],owner_values(ready.env.db)['DB_OWNER_PASSWORD']):assert value not in result.stdout+result.stderr
    finally:owner.unlink();web.unlink();worker.unlink()
