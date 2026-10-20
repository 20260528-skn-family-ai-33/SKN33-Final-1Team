"""Synthetic, metadata-only rows forming a complete valid FK graph in a test DB."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.app.persistence.schema import metadata
from backend.tests.test_review_migration_integration import service_run, service_attempt, service_result


def insert_row(connection, name, values):
    table = metadata.tables['public.' + name]
    row = connection.execute(table.insert().values(**values).returning(table)).mappings().one()
    return dict(row)


def row_where(table, row):
    from sqlalchemy import and_
    return and_(*(c == row[c.name] for c in table.primary_key.columns))


def create_graph(connection, *, user=None, project=None, version_no=1):
    now = datetime.now(timezone.utc)
    graph = {}
    if user is None:
        user = insert_row(connection, 'users', {'user_id': uuid4(), 'account_kind': 'developer', 'display_name': 'C06 synthetic developer'})
    if project is None:
        project = insert_row(connection, 'projects', {'project_id': uuid4(), 'created_by': user['user_id'], 'name': 'C06 synthetic project'})
        graph['project_members'] = insert_row(connection, 'project_members', {'project_id': project['project_id'], 'user_id': user['user_id'], 'role': 'owner'})
    else:
        graph['project_members'] = {'project_id': project['project_id'], 'user_id': user['user_id']}
    graph['users'], graph['projects'] = user, project
    project_id, user_id = project['project_id'], user['user_id']
    graph['user_sessions'] = insert_row(connection, 'user_sessions', {'session_id': uuid4(), 'user_id': user_id,
        'token_hash': uuid4().hex*2, 'created_at': now, 'expires_at': now + timedelta(hours=8)})
    files = {}
    for role in ('original_image','display_image','ocr_input','source_json','import_manifest','schema_specification','validation_schema'):
        files[role] = insert_row(connection, 'stored_files', {'file_id': uuid4(), 'project_id': project_id,
            'file_role': role, 'storage_key': 'c06-synthetic/'+uuid4().hex, 'byte_size': 1,
            'detected_mime': 'image/png' if role in ('original_image','display_image','ocr_input') else None,
            'sha256': 'a'*64, 'created_by': user_id})
    graph['stored_files'] = files['original_image']; graph['files'] = files
    graph['import_packages'] = insert_row(connection, 'import_packages', {'import_id': uuid4(), 'project_id': project_id,
        'manifest_file_id': files['import_manifest']['file_id'], 'source_namespace': 'c06-synthetic', 'imported_by': user_id})
    # Resolve the file/import cycle without a deferred FK or disabling constraints.
    for file in files.values():
        connection.execute(metadata.tables['public.stored_files'].update().where(
            metadata.tables['public.stored_files'].c.file_id == file['file_id']).values(import_package_id=graph['import_packages']['import_id']))
        file['import_package_id'] = graph['import_packages']['import_id']
    graph['assets'] = insert_row(connection, 'assets', {'asset_id': uuid4(), 'project_id': project_id, 'name': 'C06 synthetic asset', 'created_by': user_id})
    graph['asset_versions'] = insert_row(connection, 'asset_versions', {'asset_version_id': uuid4(), 'project_id': project_id,
        'asset_id': graph['assets']['asset_id'], 'version_no': 1, 'original_file_id': files['original_image']['file_id'],
        'display_file_id': files['display_image']['file_id'], 'original_sha256': 'a'*64, 'original_width': 100,
        'original_height': 100, 'display_width': 100, 'display_height': 100, 'created_by': user_id})
    asset_id = graph['asset_versions']['asset_version_id']
    context = {'project_id': project_id, 'asset_version_id': asset_id}
    graph['image_coordinate_frames'] = insert_row(connection, 'image_coordinate_frames', {**context, 'frame_id': uuid4(),
        'file_id': files['display_image']['file_id'], 'frame_kind': 'display', 'coordinate_space': 'normalized_original_pixels', 'width': 100, 'height': 100})
    graph['record_aliases'] = insert_row(connection, 'record_aliases', {**context, 'alias_id': uuid4(),
        'import_package_id': graph['import_packages']['import_id'], 'namespace': 'c06-synthetic', 'external_record_id': uuid4().hex})
    graph['schema_definitions'] = insert_row(connection, 'schema_definitions', {'schema_id': uuid4(), 'schema_name': 'c06-synthetic-'+uuid4().hex,
        'schema_version': 'fixture-v1', 'specification_file_id': files['schema_specification']['file_id'],
        'validation_schema_file_id': files['validation_schema']['file_id'], 'schema_sha256': 'a'*64,
        'availability_state': 'available', 'access_scope': 'service_shared'})
    graph['audit_events'] = insert_row(connection, 'audit_events', {'event_id': uuid4(), 'project_id': project_id,
        'actor_kind': 'developer', 'actor_user_id': user_id, 'action': 'fixture.graph', 'target_type': 'fixture'})
    graph['criteria_sets'] = insert_row(connection, 'criteria_sets', {'criteria_set_id': uuid4(), 'project_id': project_id, 'name': 'C06 synthetic criteria', 'created_by': user_id})
    graph['criteria_drafts'] = insert_row(connection, 'criteria_drafts', {'draft_id': uuid4(), 'project_id': project_id,
        'criteria_set_id': graph['criteria_sets']['criteria_set_id'], 'target_asset_version_id': asset_id,
        'raw_requirements': '합성 기준', 'items_jsonb': [{'id':'one','text':'합성 기준','selected':True}], 'updated_by': user_id})
    graph['criteria_versions'] = insert_row(connection, 'criteria_versions', {'criteria_version_id': uuid4(), 'project_id': project_id,
        'criteria_set_id': graph['criteria_sets']['criteria_set_id'], 'target_asset_version_id': asset_id,
        'source_draft_id': graph['criteria_drafts']['draft_id'], 'source_draft_revision': 1, 'source_draft_hash': 'b'*64,
        'source_requirements_text': '합성 기준', 'version_no': version_no, 'criteria_hash': 'c'*64, 'hash_version': 'fixture-content',
        'origin_kind': 'service_confirmation', 'confirmation_state': 'confirmed', 'confirmed_by': user_id, 'confirmed_at': now,
        'confirmation_key': uuid4(), 'confirmation_request_hash': 'd'*64, 'request_hash_version': 'fixture-request', 'sealed_at': now})
    criteria_id = graph['criteria_versions']['criteria_version_id']
    graph['criteria_items'] = insert_row(connection, 'criteria_items', {'criteria_item_id': uuid4(), 'project_id': project_id,
        'target_asset_version_id': asset_id, 'criteria_version_id': criteria_id, 'external_item_id': 'one', 'position': 1,
        'category': 'information', 'text': '합성 기준', 'source_asset_version_id': asset_id})
    run = {**context, **service_run(), 'run_id': uuid4(), 'criteria_version_id': criteria_id,
           'requested_by': user_id, 'import_package_id': graph['import_packages']['import_id'], 'source_file_id': files['source_json']['file_id']}
    graph['review_runs'] = insert_row(connection, 'review_runs', run)
    graph['review_attempts'] = insert_row(connection, 'review_attempts', {**context, **service_attempt('succeeded'),
        'attempt_id': uuid4(), 'run_id': run['run_id'], 'attempt_no': 1})
    attempt_id = graph['review_attempts']['attempt_id']
    graph['ocr_runs'] = insert_row(connection, 'ocr_runs', {**context, 'ocr_run_id': uuid4(), 'attempt_id': attempt_id,
        'import_package_id': graph['import_packages']['import_id'], 'schema_id': graph['schema_definitions']['schema_id'],
        'source_file_id': files['source_json']['file_id'], 'input_file_id': files['ocr_input']['file_id'],
        'frame_id': graph['image_coordinate_frames']['frame_id'], 'coordinate_space': 'normalized_original_pixels',
        'execution_status': 'completed', 'started_at': now, 'completed_at': now, 'sealed_at': now})
    ocr_id = graph['ocr_runs']['ocr_run_id']
    graph['ocr_tokens'] = insert_row(connection, 'ocr_tokens', {**context, 'token_id': uuid4(), 'ocr_run_id': ocr_id,
        'external_token_id':'1','external_token_type':'string','token_order':0,'text':'합성 문구','source_record_jsonb':{'id':'1'}})
    graph['poster_extractions'] = insert_row(connection, 'poster_extractions', {**context, 'extraction_id': uuid4(),
        'schema_id': graph['schema_definitions']['schema_id'], 'ocr_run_id': ocr_id, 'import_package_id': graph['import_packages']['import_id'],
        'source_file_id': files['source_json']['file_id'], 'version_no': 1, 'method':'fixture','payload_jsonb':{},
        'compatibility_state':'native','projection_version':'fixture','sealed_at':now})
    graph['poster_field_observations'] = insert_row(connection, 'poster_field_observations', {**context, 'observation_id':uuid4(),
        'extraction_id':graph['poster_extractions']['extraction_id'],'field_code':'title','field_path':'/title','adapter_version':'fixture'})
    graph['item_results'] = insert_row(connection, 'item_results', {**context, **service_result(), 'result_id':uuid4(),
        'run_id':run['run_id'],'attempt_id':attempt_id,'criteria_version_id':criteria_id,'criteria_item_id':graph['criteria_items']['criteria_item_id'],
        'ocr_run_id':ocr_id,'extraction_id':graph['poster_extractions']['extraction_id']})
    evidence={**context,'evidence_id':uuid4(),'observation_id':graph['poster_field_observations']['observation_id'],
        'source_file_id':files['source_json']['file_id'],'frame_id':graph['image_coordinate_frames']['frame_id'],
        'source_ocr_run_id':ocr_id,'source_kind':'ocr','method_code':'fixture'}
    graph['evidence_records']=insert_row(connection,'evidence_records',evidence)
    graph['result_evidence']=insert_row(connection,'evidence_records',{**evidence,'evidence_id':uuid4(),
        'observation_id':None,'item_result_id':graph['item_results']['result_id']})
    graph['evidence_ocr_tokens']=insert_row(connection,'evidence_ocr_tokens',{**context,'evidence_id':evidence['evidence_id'],
        'token_id':graph['ocr_tokens']['token_id'],'ocr_run_id':ocr_id,'token_order':0})
    graph['run_item_selections']=insert_row(connection,'run_item_selections',{**context,'run_id':run['run_id'],
        'criteria_version_id':criteria_id,'criteria_item_id':graph['criteria_items']['criteria_item_id'],
        'result_id':graph['item_results']['result_id'],'selection_kind':'pipeline_final'})
    # Resolve run/attempt pointers only after the referenced attempt exists.
    completed={**service_run('completed'),'requested_by':user_id,'selected_attempt_id':attempt_id}
    connection.execute(metadata.tables['public.review_runs'].update().where(metadata.tables['public.review_runs'].c.run_id==run['run_id']).values(**completed))
    graph['review_runs'].update(completed)
    active_run={**run,**service_run(),'run_id':uuid4(),'requested_by':user_id}
    graph['active_run']=insert_row(connection,'review_runs',active_run)
    graph['active_attempt']=insert_row(connection,'review_attempts',{**context,**service_attempt(),
        'attempt_id':uuid4(),'run_id':active_run['run_id'],'attempt_no':1})
    running={**service_run('running'),'requested_by':user_id,'active_attempt_id':graph['active_attempt']['attempt_id']}
    connection.execute(metadata.tables['public.review_runs'].update().where(metadata.tables['public.review_runs'].c.run_id==active_run['run_id']).values(**running))
    graph['active_run'].update(running)
    return graph
