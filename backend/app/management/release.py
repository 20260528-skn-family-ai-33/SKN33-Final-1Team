"""Fresh pre-cutover snapshot checks. Reports evidence, never switches a service."""
import hashlib
from .io import Tree,OpsError,json_bytes
from .backup import Backups,snapshot,graph_check,secret_hashes
from .maintenance import offline,frozen_database
from ..worker import worker_profile
from ..v1_config import load_v1_settings
from ..execution.worker_config import QueuedPipeline
from ..execution.pipeline import code_revision
from ..web import prepare_build
from scripts.run_v1 import launch_options


def profiles(web,worker):
    app=load_v1_settings(web);job=worker_profile(worker)
    launch_options(app,web,'0.0.0.0',8000)
    if (not app.secure or job.environment!='production' or app.developer_login
        or app.review_executor!='postgres_worker' or not app.frontend_dist
        or any(worker.get(k) for k in ('CSRF_SECRET','CURSOR_SECRET','AUTH_ALLOWED_ORIGINS','AUTH_ALLOWED_HOSTS'))):
        raise OpsError('RELEASE_EXPLICIT_PRODUCTION_PROFILES_REQUIRED')
    a,b=app.database,job.database
    if ((a.host,a.port,a.database,a.username,a.password.reveal(),app.storage_root)
        !=(b.host,b.port,b.database,b.username,b.password.reveal(),job.storage_root)):
        raise OpsError('RELEASE_WEB_WORKER_TARGET_MISMATCH')
    return app,job


def check(engine,settings,storage,backup,web,worker):
    app,job=profiles(web,worker)
    if (app.database.host,app.database.port,app.database.database,app.database.username,app.storage_root)!=(settings.host,settings.port,settings.database,settings.app_user,storage.path):
        raise OpsError('RELEASE_OWNER_TARGET_MISMATCH')
    manifest=Backups(engine,settings,None).inspect(backup)
    if not manifest['restore_ready']:raise OpsError('RELEASE_ACTUAL_RESTORE_DRILL_REQUIRED')
    if manifest['source']!=dict(database=settings.database,owner_role=settings.owner_user,app_role=settings.app_user):
        raise OpsError('RELEASE_BACKUP_TARGET_MISMATCH')
    if secret_hashes(web)[1]!=manifest['key_fingerprints']:raise OpsError('RELEASE_BACKUP_SETTINGS_CHANGED')
    config=QueuedPipeline(app.review_config_file).describe()
    prepare_build(app.frontend_dist)
    with Tree(app.frontend_dist) as build:frontend=build.inventory(max_files=1000,max_bytes=128*1024**2,max_path_bytes=1024)
    with offline(engine,storage,drain_seconds=0),frozen_database(engine) as c:
        if snapshot(c)!=manifest['tables']:raise OpsError('RELEASE_BACKUP_DATABASE_STALE')
        checks=graph_check(c,storage,settings.app_user)
        if c.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM public.users WHERE account_kind='developer' AND is_active)").scalar_one():raise OpsError('RELEASE_DEVELOPER_ACTIVE')
        if c.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM public.projects p WHERE p.archived_at IS NULL AND NOT EXISTS(SELECT 1 FROM public.project_members m JOIN public.users u ON u.user_id=m.user_id WHERE m.project_id=p.project_id AND m.role='owner' AND m.is_active AND u.is_active AND u.account_kind='email'))").scalar_one():raise OpsError('RELEASE_EMAIL_OWNER_REQUIRED')
        pending={name:c.exec_driver_sql(sql).scalar_one() for name,sql in {
            'queued_runs':"SELECT count(*) FROM public.review_runs WHERE execution_status='queued'",
            'unfinished_imports':"SELECT count(*) FROM public.import_packages WHERE import_state<>'completed'"}.items()}
        with Tree(storage.path,private=True) as source:
            files=[v for v in source.inventory(max_files=10000,max_bytes=4*1024**3,max_path_bytes=1024) if not v['relative_path'].startswith('locks/')]
        if files!=manifest['storage_files']:raise OpsError('RELEASE_BACKUP_FILES_STALE')
        for item in manifest['registered_files']:
            if item['state']!='stored' or not item['available']:raise OpsError('RELEASE_FILE_UNAVAILABLE')
            with storage.verified(item['storage_key'],int(item['byte_size']),item['sha256']):pass
    return dict(contract_version='cc-release-check-v1',state='preflight_passed',schema_revision='M008',
        backup_id=manifest['backup_id'],restore_drills=manifest['verified_restore_drills'],checks=checks,pending=pending,
        compatibility=dict(code_revision=code_revision(),frontend_sha256=hashlib.sha256(json_bytes(frontend)).hexdigest(),
            worker_config_sha256=hashlib.sha256(json_bytes(config)).hexdigest(),schema_structure_sha256=checks['schema']['structure_sha256']),
        single_writer='v1_only; legacy writes return 410; legacy reads are not mounted',
        worker_runtime_verified=False,production_cutover_performed=False,
        remaining_operator_checks=['Verify this exact Linux image/model through an actual local OCR run.',
            'Verify target TLS, proxy/network isolation and backup encryption/off-host retention/scheduling.',
            'Stop old writers, preserve original history, then select one v1 deployment; never reopen legacy writes after v1 data.'])
