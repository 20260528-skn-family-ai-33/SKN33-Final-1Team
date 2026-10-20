"""Behavioral probes use real deferred constraints, never disabled triggers/FKs."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_guard_migration_integration import guards_database  # noqa: F401
from backend.tests.test_review_migration_integration import service_run
from backend.tests.test_identity_migration_integration import owner_rollback
from backend.tests.guard_fixtures import (insert_row,update_row,clone_ocr,clone_extraction,clone_criteria,start_run,finish_run)

pytestmark=pytest.mark.db_integration


def flush(connection):
    connection.exec_driver_sql('SET CONSTRAINTS ALL IMMEDIATE')


def rejected(error,rule=None):
    assert error.value.orig.sqlstate=='23514'
    if rule:assert error.value.orig.diag.constraint_name==rule


IMMUTABLE=[('schema_definitions','schema_version','changed'),('stored_files','sha256','b'*64),
 ('import_packages','source_namespace','changed'),('ocr_runs','coordinate_space','changed'),
 ('poster_extractions','payload_jsonb',{'edited':True}),('criteria_versions','criteria_hash','e'*64),
 ('criteria_drafts','target_asset_version_id','@uuid'),('review_runs','requested_config',{'edited':True}),
 ('review_attempts','worker_token','@uuid')]


@pytest.mark.parametrize('table,column,value',IMMUTABLE,ids=[r[0] for r in IMMUTABLE])
def test_transition_rejects_immutable_identity_or_input(guards_database,table,column,value):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as error:
            update_row(connection,table,deepcopy(g[table]),**{column:uuid4() if value=='@uuid' else value})
        rejected(error,'tr_'+table+'_transition')


APPENDS=['ocr_tokens','poster_field_observations','criteria_items','item_results','run_item_selections','evidence_records','evidence_ocr_tokens']


@pytest.mark.parametrize('table',APPENDS)
def test_sealed_roots_reject_all_seven_late_child_paths(guards_database,table):
    _,owner,_,g,_=guards_database
    body={**g[table]}
    key={'ocr_tokens':'token_id','poster_field_observations':'observation_id','criteria_items':'criteria_item_id',
         'item_results':'result_id','evidence_records':'evidence_id'}.get(table)
    if key:body[key]=uuid4()
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as error:insert_row(connection,table,body)
        rejected(error)


@pytest.mark.parametrize('kind',('ocr','attempt'))
def test_new_service_roots_cannot_attach_to_completed_run(guards_database,kind):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as error:
            if kind=='ocr':clone_ocr(connection,g,attempt_id=g['review_attempts']['attempt_id'])
            else:insert_row(connection,'review_attempts',{**g['active_attempt'],'attempt_id':uuid4(),'attempt_no':2,'run_id':g['review_runs']['run_id']})
        rejected(error)


@pytest.mark.parametrize('kind',('ocr','extraction','criteria'))
def test_unsealed_snapshots_cannot_commit(guards_database,kind):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        if kind=='ocr':clone_ocr(connection,g)
        elif kind=='extraction':clone_extraction(connection,g)
        else:clone_criteria(connection,g,seal=False)
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,{'ocr':'ct_ocr_snapshot','extraction':'ct_extraction_snapshot','criteria':'ct_criteria_snapshot'}[kind])


@pytest.mark.parametrize('positions',((),(2,3),tuple(range(1,102))),ids=('empty','gap','over_100'))
def test_criteria_snapshot_requires_one_to_100_contiguous_items(guards_database,positions):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        clone_criteria(connection,g,positions)
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,'ct_criteria_snapshot')


@pytest.mark.parametrize('positions',((1,),tuple(range(1,101))),ids=('one','100'))
def test_valid_criteria_cardinalities_and_final_row_recheck(guards_database,positions):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        clone_criteria(connection,g,positions)
        flush(connection)


@pytest.mark.parametrize('patch',({'coordinate_space':'wrong-space'},{'source_file_id':'@image'}),ids=('space','file_role'))
def test_ocr_metadata_context_must_match_frame_and_file_roles(guards_database,patch):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        body={k:g['stored_files']['file_id'] if v=='@image' else v for k,v in patch.items()}
        r=clone_ocr(connection,g,**body)
        update_row(connection,'ocr_runs',r,sealed_at=datetime.now(timezone.utc))
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,'ct_ocr_snapshot')


@pytest.mark.parametrize('geometry',('bbox','polygon'))
def test_ocr_geometry_is_bounded_by_actual_frame(guards_database,geometry):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        r=clone_ocr(connection,g)
        insert_row(connection,'ocr_tokens',{**g['ocr_tokens'],'token_id':uuid4(),'ocr_run_id':r['ocr_run_id'],
            **({'bbox_xyxy':[0,0,101,100]} if geometry=='bbox' else {'polygon':[[0,0],[101,0],[1,1]]})})
        update_row(connection,'ocr_runs',r,sealed_at=datetime.now(timezone.utc))
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,'ct_ocr_snapshot')


@pytest.mark.parametrize('fault',('missing_selection','hash','ocr_attempt','extraction_ocr','selected_state','summary'))
def test_completed_run_requires_whole_same_attempt_context(guards_database,fault):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        run,attempt=start_run(connection,g)
        patch={}
        if fault=='hash':patch['input_manifest_hash']='f'*64
        elif fault=='ocr_attempt':patch.update(ocr_run_id=g['ocr_runs']['ocr_run_id'],extraction_id=g['poster_extractions']['extraction_id'])
        elif fault=='extraction_ocr':
            o=clone_ocr(connection,g,attempt_id=attempt['attempt_id'],execution_status='completed',
                started_at=datetime.now(timezone.utc),completed_at=datetime.now(timezone.utc))
            update_row(connection,'ocr_runs',o,sealed_at=datetime.now(timezone.utc))
            patch.update(ocr_run_id=o['ocr_run_id'],extraction_id=g['poster_extractions']['extraction_id'])
        finish_run(connection,g,run,attempt,result_patch=patch,select=fault!='missing_selection',
            attempt_status='failed' if fault=='selected_state' else 'succeeded',verdict='pass' if fault=='summary' else 'review')
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,'ct_review_integrity')


@pytest.mark.parametrize('fault',('orphan_active','lost_active','worker_without_lease'))
def test_active_attempt_and_run_pointer_must_commit_together(guards_database,fault):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        if fault=='worker_without_lease':
            # The insertion guard rejects a missing worker lease before pointer creation.
            run={**g['active_run'],**service_run(),
                 'run_id':uuid4(),'requested_by':g['users']['user_id'],'executor_kind':'postgres_worker','active_attempt_id':None,'started_at':None}
            insert_row(connection,'review_runs',run)
            with pytest.raises(DBAPIError) as error:insert_row(connection,'review_attempts',{**g['active_attempt'],'attempt_id':uuid4(),'run_id':run['run_id']})
        else:
            run,attempt=start_run(connection,g,running=fault!='orphan_active')
            if fault!='orphan_active':update_row(connection,'review_attempts',attempt,execution_status='failed',finished_at=datetime.now(timezone.utc))
            with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error)


@pytest.mark.parametrize('change',({'raw_requirements':'changed'},{'items_jsonb':[]},{'source_documents_jsonb':[{'file':'changed'}]}))
def test_draft_content_requires_exact_revision_increment(guards_database,change):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as error:update_row(connection,'criteria_drafts',deepcopy(g['criteria_drafts']),**change)
        rejected(error,'tr_criteria_drafts_transition')


def test_draft_edit_archive_and_stale_confirmation(guards_database):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        d=deepcopy(g['criteria_drafts'])
        update_row(connection,'criteria_drafts',d,raw_requirements='new',revision=d['revision']+1)
        update_row(connection,'criteria_drafts',d,state='archived')
        with pytest.raises(DBAPIError) as error:update_row(connection,'criteria_drafts',d,state='editing')
        rejected(error,'tr_criteria_drafts_transition')


@pytest.mark.parametrize('state',('completed','failed','imported'))
def test_terminal_ocr_remains_immutable_after_seal(guards_database,state):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        row=clone_ocr(connection,g,execution_status=state,**({'started_at':datetime.now(timezone.utc),'completed_at':datetime.now(timezone.utc),
            'error_jsonb':{'code':'fixture'},'attempt_id':g['active_attempt']['attempt_id']} if state!='imported' else {}))
        update_row(connection,'ocr_runs',row,sealed_at=datetime.now(timezone.utc))
        with pytest.raises(DBAPIError) as error:update_row(connection,'ocr_runs',row,engine_name='late')
        rejected(error,'tr_ocr_runs_transition')


@pytest.mark.parametrize('fault',('role','hash','regression','replace'))
def test_schema_files_and_available_definition_cannot_be_rewritten(guards_database,fault):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as error:
            if fault in ('role','hash'):
                insert_row(connection,'schema_definitions',{**g['schema_definitions'],'schema_id':uuid4(),'schema_name':'fixture-'+uuid4().hex,
                    **({'validation_schema_file_id':g['stored_files']['file_id']} if fault=='role' else {'schema_sha256':'b'*64})})
            else:update_row(connection,'schema_definitions',deepcopy(g['schema_definitions']),
                **({'availability_state':'metadata_only','access_scope':'metadata_only'} if fault=='regression' else {'field_catalog_jsonb':{'changed':True}}))
        rejected(error,'tr_schema_definitions_transition')


def test_schema_can_become_available_once_and_shared_access_can_be_revoked(guards_database):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        row=insert_row(connection,'schema_definitions',{**g['schema_definitions'],'schema_id':uuid4(),'schema_name':'fixture-'+uuid4().hex,
            'availability_state':'metadata_only','access_scope':'metadata_only','validation_schema_file_id':None,'schema_sha256':None})
        update_row(connection,'schema_definitions',row,availability_state='available',access_scope='service_shared',
            validation_schema_file_id=g['files']['validation_schema']['file_id'],schema_sha256='a'*64)
        update_row(connection,'schema_definitions',row,access_scope='metadata_only');flush(connection)


@pytest.mark.parametrize('fault',('ocr_source','image_file','bounds'))
def test_observation_evidence_must_use_real_input_ocr_and_frame(guards_database,fault):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        root=clone_extraction(connection,g)
        obs=insert_row(connection,'poster_field_observations',{**g['poster_field_observations'],
            'observation_id':uuid4(),'extraction_id':root['extraction_id']})
        body={**g['evidence_records'],'evidence_id':uuid4(),'observation_id':obs['observation_id']}
        if fault=='ocr_source':
            o=clone_ocr(connection,g);update_row(connection,'ocr_runs',o,sealed_at=datetime.now(timezone.utc))
            body['source_ocr_run_id']=o['ocr_run_id']
        elif fault=='image_file':body.update(source_kind='image',source_ocr_run_id=None,source_file_id=g['stored_files']['file_id'])
        else:body['bbox_xyxy']=[0,0,101,100]
        insert_row(connection,'evidence_records',body)
        update_row(connection,'poster_extractions',root,sealed_at=datetime.now(timezone.utc))
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,'cc_evidence_bounds' if fault=='bounds' else 'cc_evidence_source')


@pytest.mark.parametrize('kind',('ocr','extraction','criteria'))
def test_real_commit_failure_rolls_back_parent_children_and_audit(guards_database,kind):
    _,owner,_,g,_=guards_database
    def counts(connection):
        return [connection.exec_driver_sql('SELECT count(*) FROM public.'+t).scalar_one()
            for t in ('ocr_runs','ocr_tokens','poster_extractions','criteria_versions','criteria_items','audit_events')]
    with owner.connect() as connection:
        before=counts(connection);connection.rollback();transaction=connection.begin()
        if kind=='ocr':
            r=clone_ocr(connection,g)
            insert_row(connection,'ocr_tokens',{**g['ocr_tokens'],'token_id':uuid4(),'ocr_run_id':r['ocr_run_id']})
        elif kind=='extraction':clone_extraction(connection,g)
        else:clone_criteria(connection,g,seal=False)
        insert_row(connection,'audit_events',{**g['audit_events'],'event_id':uuid4(),'action':'fixture.must_rollback'})
        with pytest.raises(DBAPIError) as error:transaction.commit()
        rejected(error,{'ocr':'ct_ocr_snapshot','extraction':'ct_extraction_snapshot','criteria':'ct_criteria_snapshot'}[kind])
        connection.rollback()
        assert counts(connection)==before


def test_imported_structure_seals_without_inventing_local_approval_or_verdict(guards_database):
    _,owner,_,g,_=guards_database
    now=datetime.now(timezone.utc)
    with owner.begin() as connection:
        version,items=clone_criteria(connection,g,origin_kind='imported',confirmation_state='unconfirmed',source_draft_id=None,
            source_draft_revision=None,source_draft_hash=None,source_requirements_text=None,confirmed_by=None,confirmed_at=None,
            confirmation_key=None,confirmation_request_hash=None,request_hash_version=None,
            import_package_id=g['import_packages']['import_id'],source_snapshot_jsonb={'reported_confirmation':'confirmed'})
        run=insert_row(connection,'review_runs',{'run_id':uuid4(),'project_id':g['projects']['project_id'],
            'asset_version_id':g['asset_versions']['asset_version_id'],'criteria_version_id':version['criteria_version_id'],
            'origin_kind':'imported_result','import_package_id':g['import_packages']['import_id'],
            'source_snapshot_jsonb':{'reported_verdict':'pass'},'execution_status':'imported','executor_kind':'imported',
            'orchestration_mode':'imported','manifest_hash_version':'fixture'})
        attempt=insert_row(connection,'review_attempts',{'attempt_id':uuid4(),'project_id':run['project_id'],
            'asset_version_id':run['asset_version_id'],'run_id':run['run_id'],'attempt_no':1,
            'execution_status':'imported','source_attempt_jsonb':{'actual_attempt_count':None}})
        result=insert_row(connection,'item_results',{'result_id':uuid4(),'project_id':run['project_id'],
            'asset_version_id':run['asset_version_id'],'run_id':run['run_id'],'attempt_id':attempt['attempt_id'],
            'criteria_version_id':version['criteria_version_id'],'criteria_item_id':items[0]['criteria_item_id'],
            'result_origin':'imported_result','execution_status':'imported','source_result_jsonb':{'verdict':'pass'}})
        insert_row(connection,'run_item_selections',{'run_id':run['run_id'],'project_id':run['project_id'],
            'asset_version_id':run['asset_version_id'],'criteria_version_id':version['criteria_version_id'],
            'criteria_item_id':items[0]['criteria_item_id'],'result_id':result['result_id'],'selection_kind':'import_projection'})
        update_row(connection,'review_runs',run,selected_attempt_id=attempt['attempt_id'],result_manifest_hash='a'*64,sealed_at=now)
    with owner.connect() as connection:
        from sqlalchemy import text
        row=connection.execute(text('SELECT r.overall_verdict,r.started_at,r.finished_at,r.progress,v.confirmation_state,v.confirmed_by,i.verdict FROM public.review_runs r JOIN public.criteria_versions v USING(criteria_version_id) JOIN public.item_results i USING(run_id) WHERE r.run_id=:id'),{'id':run['run_id']}).one()
        assert tuple(row)==(None,None,None,None,'unconfirmed',None,None)


@pytest.mark.parametrize('changed',('revision','archived'))
def test_new_run_cannot_accept_stale_or_archived_confirmation(guards_database,changed):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        d=deepcopy(g['criteria_drafts'])
        update_row(connection,'criteria_drafts',d,**({'raw_requirements':'changed','revision':d['revision']+1} if changed=='revision' else {'state':'archived'}))
        with pytest.raises(DBAPIError) as error:start_run(connection,g)
        rejected(error,'tr_review_runs_transition')


@pytest.mark.parametrize('patch',({'source_draft_revision':99},{'source_requirements_text':'mismatched snapshot'}))
def test_confirmation_must_match_locked_source_draft_snapshot(guards_database,patch):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        clone_criteria(connection,g,**patch)
        with pytest.raises(DBAPIError) as error:flush(connection)
        rejected(error,'ct_criteria_snapshot')


def test_ocr_creation_can_progress_and_failure_seal_in_one_transaction(guards_database):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        o=clone_ocr(connection,g,execution_status='queued')
        update_row(connection,'ocr_runs',o,execution_status='running',started_at=datetime.now(timezone.utc))
        update_row(connection,'ocr_runs',o,execution_status='failed',completed_at=datetime.now(timezone.utc),error_jsonb={'code':'fixture'})
        update_row(connection,'ocr_runs',o,sealed_at=datetime.now(timezone.utc));flush(connection)


@pytest.mark.parametrize('target',(None,'not_dispatched','dispatching'))
def test_submitted_dispatch_cannot_be_cleared_or_rewound(guards_database,target):
    _,owner,_,g,_=guards_database
    with owner_rollback(owner) as connection:
        _,a=start_run(connection,g)
        for state in ('not_dispatched','dispatching','submitted'):update_row(connection,'review_attempts',a,dispatch_state=state)
        with pytest.raises(DBAPIError) as error:update_row(connection,'review_attempts',a,dispatch_state=target)
        rejected(error,'tr_review_attempts_transition')
