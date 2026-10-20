"""Lineage, ownership, import and lease transitions under the promoted M007."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from backend.app.persistence.schema import metadata
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_guard_migration_integration import guards_database  # noqa: F401
from backend.tests.test_identity_migration_integration import owner_rollback
from backend.tests.guard_fixtures import insert_row,update_row,start_run,clone_criteria
from backend.tests.test_guard_rules_integration import flush,rejected

pytestmark=pytest.mark.db_integration
LINEAGES=[('assets','asset_id','parent_asset_id'),('stored_files','file_id','parent_file_id'),
 ('image_coordinate_frames','frame_id','parent_frame_id'),('import_packages','import_id','replaces_import_id'),
 ('poster_extractions','extraction_id','predecessor_id'),('criteria_versions','criteria_version_id','source_criteria_version_id'),
 ('review_runs','run_id','retry_of_run_id')]


@pytest.mark.parametrize('table,key,parent',LINEAGES,ids=[x[0] for x in LINEAGES])
def test_single_statement_multirow_lineage_cycles_cannot_commit(guards_database,table,key,parent):
    _,owner,_,g,_=guards_database
    ids=[uuid4(),uuid4()];now=datetime.now(timezone.utc);rows=[]
    for i in range(2):
        body={**g[table],key:ids[i],parent:ids[1-i]}
        if table=='stored_files':body.update(storage_key='c07b-cycle/'+uuid4().hex)
        elif table=='image_coordinate_frames':body.update(transform_jsonb={'method':'fixture'})
        elif table=='poster_extractions':body.update(version_no=100+i,sealed_at=now)
        elif table=='criteria_versions':body.update(version_no=100+i,confirmation_key=uuid4(),sealed_at=None)
        elif table=='review_runs':
            body.update({c.name:None for c in metadata.tables['public.review_runs'].c if c.nullable})
            body.update(origin_kind='legacy_result',execution_status='imported',executor_kind='imported',orchestration_mode='imported',
                retry_of_run_id=ids[1-i],source_snapshot_jsonb={},sealed_at=now,result_manifest_hash='a'*64,manifest_hash_version='fixture')
        rows.append(body)
    with owner_rollback(owner) as connection:
        connection.execute(metadata.tables['public.'+table].insert().values(rows))
        if table=='criteria_versions':
            for body in rows:
                insert_row(connection,'criteria_items',{**g['criteria_items'],'criteria_item_id':uuid4(),'criteria_version_id':body[key]})
                update_row(connection,table,body,sealed_at=now)
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,'ct_lineage_'+table)


@pytest.mark.parametrize('table',('poster_extractions','criteria_versions'))
def test_predecessor_version_must_be_smaller(guards_database,table):
    _,owner,_,g,other=guards_database
    with owner_rollback(owner) as connection:
        if table=='criteria_versions':
            clone_criteria(connection,g,version_no=99,source_criteria_version_id=clone_criteria(connection,g,version_no=100)[0]['criteria_version_id'])
        else:
            from backend.tests.guard_fixtures import clone_extraction
            parent=clone_extraction(connection,g,version_no=100,sealed_at=datetime.now(timezone.utc))
            clone_extraction(connection,g,version_no=99,predecessor_id=parent['extraction_id'],sealed_at=datetime.now(timezone.utc))
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,'ct_lineage_'+table)


@pytest.mark.parametrize('kind',('membership','user','new_project'))
def test_project_cannot_commit_without_active_user_owner(guards_database,kind):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        if kind=='membership':update_row(connection,'project_members',deepcopy(g['project_members']),role='viewer')
        elif kind=='user':update_row(connection,'users',deepcopy(g['users']),is_active=False)
        else:insert_row(connection,'projects',{'project_id':uuid4(),'created_by':g['users']['user_id'],'name':'no owner fixture'})
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,'ct_project_owner')


def completed_package(connection,g):
    file=insert_row(connection,'stored_files',{**g['files']['import_manifest'],'file_id':uuid4(),
        'import_package_id':None,'storage_key':'c07b-manifest/'+uuid4().hex})
    package=insert_row(connection,'import_packages',{**g['import_packages'],'import_id':uuid4(),'manifest_file_id':file['file_id']})
    update_row(connection,'stored_files',file,import_package_id=package['import_id'])
    update_row(connection,'import_packages',package,import_state='completed',package_fingerprint='a'*64,
        fingerprint_version='fixture',hash_check_state='matched',local_validation_jsonb={})
    return package,file


@pytest.mark.parametrize('fault',('unlinked','missing','wrong_role'))
def test_import_completion_requires_linked_stored_manifest(guards_database,fault):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        package,file=completed_package(connection,g)
        if fault=='unlinked':
            # A different, valid file is present but belongs to the original package.
            # Build a new candidate instead of replacing the already fixed manifest.
            package=insert_row(connection,'import_packages',{**g['import_packages'],'import_id':uuid4(),
                'import_state':'completed','package_fingerprint':'b'*64,'fingerprint_version':'fixture','hash_check_state':'matched','local_validation_jsonb':{}})
        elif fault=='missing':update_row(connection,'stored_files',file,state='missing')
        else:
            package=insert_row(connection,'import_packages',{**g['import_packages'],'import_id':uuid4(),
                'manifest_file_id':g['stored_files']['file_id'],'import_state':'completed','package_fingerprint':'c'*64,
                'fingerprint_version':'fixture','hash_check_state':'matched','local_validation_jsonb':{}})
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,'ct_import_integrity')


def test_completed_import_preserves_history_when_file_later_goes_missing(guards_database):
    _,owner,_,g,_=guards_database
    with owner.begin() as connection:package,file=completed_package(connection,g)
    with owner.begin() as connection:update_row(connection,'stored_files',file,state='missing')
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as error:insert_row(connection,'stored_files',{**g['stored_files'],
            'file_id':uuid4(),'storage_key':'c07b-late/'+uuid4().hex,'import_package_id':package['import_id']})
        rejected(error,'tr_stored_files_import_gate')
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as error:update_row(connection,'import_packages',package,local_validation_jsonb={'edit':True})
        rejected(error,'tr_import_packages_transition')


@pytest.mark.parametrize('target',('clear','replace'))
def test_file_import_binding_is_one_time(guards_database,target):
    _,owner,_,g,other=guards_database
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as error:update_row(connection,'stored_files',deepcopy(g['stored_files']),
            import_package_id=None if target=='clear' else other['import_packages']['import_id'])
        rejected(error,'tr_stored_files_transition')


@pytest.mark.parametrize('audited',(False,True))
def test_failed_import_revalidation_requires_explicit_audit(guards_database,audited):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        p=deepcopy(g['import_packages']);update_row(connection,'import_packages',p,import_state='failed')
        if audited:
            insert_row(connection,'audit_events',{**g['audit_events'],'event_id':uuid4(),'action':'import.revalidate',
                'target_type':'import_package','target_id':p['import_id']})
            update_row(connection,'import_packages',p,import_state='received');flush(connection)
        else:
            with pytest.raises(DBAPIError) as error:update_row(connection,'import_packages',p,import_state='received')
            rejected(error,'tr_import_packages_transition')


@pytest.mark.parametrize('operation',('heartbeat','result','succeed'))
def test_expired_worker_authority_cannot_be_revived_or_write_late_results(guards_database,operation):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        run,a=start_run(connection,g,worker=True,lease_seconds=.5)
        connection.exec_driver_sql('SELECT pg_sleep(0.6)')
        with pytest.raises(DBAPIError) as error:
            if operation=='heartbeat':update_row(connection,'review_attempts',a,heartbeat_at=datetime.now(timezone.utc),lease_expires_at=datetime.now(timezone.utc)+timedelta(seconds=120))
            elif operation=='succeed':update_row(connection,'review_attempts',a,execution_status='succeeded',finished_at=datetime.now(timezone.utc))
            else:insert_row(connection,'item_results',{**g['item_results'],'result_id':uuid4(),'run_id':run['run_id'],'attempt_id':a['attempt_id']})
        rejected(error)


def test_live_worker_can_heartbeat_and_finish_safely(guards_database):
    from backend.tests.guard_fixtures import finish_run
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        run,a=start_run(connection,g,worker=True)
        update_row(connection,'review_attempts',a,heartbeat_at=datetime.now(timezone.utc),lease_expires_at=datetime.now(timezone.utc)+timedelta(seconds=180))
        finish_run(connection,g,run,a);flush(connection)


@pytest.mark.parametrize('fault',('valid','background','unknown','dispatched','not_retryable','limit','malformed'))
def test_requeue_requires_safe_closed_attempt_and_fixed_policy(guards_database,fault):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        run,a=start_run(connection,g,worker=fault!='background',retry_policy_jsonb={'max_attempts':1 if fault=='limit' else 'bad' if fault=='malformed' else 3})
        update_row(connection,'review_attempts',a,dispatch_state='not_dispatched')
        if fault=='dispatched':update_row(connection,'review_attempts',a,dispatch_state='dispatching')
        update_row(connection,'review_attempts',a,execution_status='unknown' if fault=='unknown' else 'failed',
            retryable=fault!='not_retryable',finished_at=datetime.now(timezone.utc))
        patch={'execution_status':'queued','current_stage':'queued','active_attempt_id':None,'available_at':datetime.now(timezone.utc)}
        if fault=='valid':update_row(connection,'review_runs',run,**patch);flush(connection)
        else:
            with pytest.raises(DBAPIError) as error:update_row(connection,'review_runs',run,**patch)
            rejected(error,'tr_review_runs_transition')


def test_guard_rejects_repeatable_read_write_skew_assumptions(guards_database):
    _,owner,_,g,_=guards_database
    with owner.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with pytest.raises(DBAPIError) as error:connection.exec_driver_sql("SELECT public.cc_lock_project('"+str(g['projects']['project_id'])+"'::uuid)")
        rejected(error,'cc_guard_isolation')
