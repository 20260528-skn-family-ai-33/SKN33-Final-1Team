"""M004 -> M005 on isolated PostgreSQL; run lifetimes and verdicts stay separate."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_identity_migration_integration import migrated, owner_rollback  # noqa: F401
from backend.tests.test_file_migration_integration import files_database, FILE_NAMES, insert  # noqa: F401
from backend.tests.test_ocr_migration_integration import ocr_database, OCR_NAMES  # noqa: F401
from backend.tests.test_criteria_migration_integration import criteria_database, CRITERIA_NAMES, record as criteria_record  # noqa: F401

pytestmark = pytest.mark.db_integration
REVIEW_NAMES = ('review_runs', 'review_attempts', 'item_results', 'run_item_selections')
KEYS = dict(zip(REVIEW_NAMES, ('run_id', 'attempt_id', 'result_id', 'run_id')))
NOW = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope='module')
def reviews_database(criteria_database, tmp_path_factory):
    from alembic import command
    from alembic.util import CommandError
    db, owner, configuration, user, project = criteria_database
    prior = ('users', 'user_sessions', 'projects', 'project_members', *FILE_NAMES, *OCR_NAMES, *CRITERIA_NAMES)
    with owner.begin() as connection:
        for name in CRITERIA_NAMES:
            insert(connection, name, criteria_record(name, project, user))
    def snapshot():
        with owner.connect() as connection:
            return (connection.exec_driver_sql("SELECT c.relname,c.oid::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY 1").all(),
                    connection.exec_driver_sql('SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2').all(),
                    [connection.exec_driver_sql(f'SELECT to_jsonb(t)::text FROM public."{name}" t ORDER BY to_jsonb(t)::text').all() for name in prior])
    before = snapshot()
    # Earlier partial revisions intentionally permit dangling test rows. Do not
    # adopt them or leave M005 installed when the following M006 validation fails.
    with pytest.raises(CommandError, match='SQLSTATE=23503'):
        command.upgrade(configuration(), 'M006')
    assert snapshot() == before
    with owner.connect() as connection:
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one() == 'M004'
    with owner.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE public.item_results (marker text)')
        connection.exec_driver_sql("INSERT INTO public.item_results VALUES ('preserve prior object')")
    with pytest.raises(CommandError, match='SQLSTATE=42P07'):
        command.upgrade(configuration(), 'M005')
    with owner.begin() as connection:
        assert connection.exec_driver_sql('SELECT marker FROM public.item_results').scalar_one() == 'preserve prior object'
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one() == 'M004'
        assert connection.exec_driver_sql("SELECT to_regclass('public.review_runs') IS NULL AND to_regclass('public.review_attempts') IS NULL").scalar_one()
        connection.exec_driver_sql('DROP TABLE public.item_results')
    assert snapshot() == before
    directory = tmp_path_factory.mktemp('c06_failed_reviews') / 'migrations'
    shutil.copytree('backend/migrations', directory, ignore=shutil.ignore_patterns('__pycache__'))
    revision = directory / 'versions/M005_reviews.py'
    revision.write_text(revision.read_text().replace('\n\ndef downgrade():', '\n    op.execute(text("SELECT 1 / 0"))\n\ndef downgrade():'))
    with pytest.raises(CommandError, match='SQLSTATE=22012'):
        command.upgrade(configuration(directory), 'M005')
    assert snapshot() == before
    command.upgrade(configuration(), 'M005')
    assert snapshot()[2] == before[2]
    yield db, owner, configuration, user, project

LATER = datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc)


def record(name, project, user):
    body = {KEYS[name]: uuid4(), 'project_id': project, 'asset_version_id': uuid4()}
    if name == 'review_runs':
        return {**body, 'criteria_version_id': uuid4(), 'origin_kind': 'legacy_result', 'execution_status': 'imported',
                'executor_kind': 'imported', 'orchestration_mode': 'imported', 'source_snapshot_jsonb': {'source_status': 'completed'}}
    if name == 'review_attempts':
        return {**body, 'run_id': uuid4(), 'attempt_no': 1, 'execution_status': 'imported', 'source_attempt_jsonb': {'actual_attempts': None}}
    if name == 'item_results':
        return {**body, 'run_id': uuid4(), 'attempt_id': uuid4(), 'criteria_version_id': uuid4(), 'criteria_item_id': uuid4(),
                'result_origin': 'legacy_result', 'execution_status': 'imported', 'source_result_jsonb': {'status': 'pass'}}
    return {**body, 'criteria_item_id': uuid4(), 'criteria_version_id': uuid4(), 'result_id': uuid4(), 'selection_kind': 'import_projection'}


def service_run(state='queued'):
    values = {'origin_kind': 'service_execution', 'requested_by': uuid4(), 'request_operation': 'create_review',
              'idempotency_key': uuid4(), 'request_hash': 'a'*64, 'request_hash_version': 'fixture-request-v1',
              'executor_kind': 'background_tasks', 'orchestration_mode': 'single_pipeline', 'workflow_version': 'fixture-workflow',
              'aggregation_policy_version': 'all_selected_v1', 'execution_target': 'local', 'requested_config': {},
              'input_manifest_hash': 'b'*64, 'manifest_hash_version': 'fixture-manifest', 'retry_policy_jsonb': {'max_attempts': 1},
              'execution_status': state, 'current_stage': state, 'available_at': None}
    if state == 'queued':
        values['available_at'] = NOW
    elif state == 'running':
        values.update(current_stage='ocr', started_at=NOW, active_attempt_id=uuid4(), progress=50)
    elif state == 'completed':
        values.update(started_at=NOW, finished_at=LATER, sealed_at=LATER, selected_attempt_id=uuid4(),
                      result_manifest_hash='c'*64, overall_verdict='review', progress=100)
    elif state == 'failed':
        values.update(finished_at=LATER, error_jsonb={'code': 'fixture-failed'})
    return values


def service_attempt(state='active'):
    return {'execution_status': state, 'worker_token': uuid4(), 'started_at': NOW,
            'finished_at': None if state == 'active' else LATER, 'stage': 'ocr'}


def service_result(state='completed'):
    return {'result_origin': 'service_execution', 'execution_status': state, 'verdict': 'review' if state == 'completed' else None,
            'reason': '실제 확인한 범위', 'checker_version': 'fixture-checker', 'input_manifest_hash': 'd'*64}


def test_m005_full_inventory_and_prior_rows_preserved(reviews_database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import CheckConstraint, UniqueConstraint, inspect
    from backend.tests.schema_helpers import metadata_before_relations
    metadata = metadata_before_relations()
    _, owner, _, _, _ = reviews_database
    with owner.connect() as connection:
        inspector = inspect(connection)
        assert set(inspector.get_table_names(schema='public')) == {t.name for t in metadata.tables.values()} | {'alembic_version'}
        assert sum(len(inspector.get_columns(t.name, schema='public')) for t in metadata.tables.values()) == 395
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one() == 'M005'
        diff = compare_metadata(MigrationContext.configure(connection, opts={'include_schemas': True, 'compare_server_default': True,
            'include_object': lambda obj, name, kind, reflected, compare_to: getattr(obj, 'schema', 'public') in ('public', None) and (kind != 'table' or name != 'alembic_version')}), metadata)
        assert diff == [], diff
        for name in REVIEW_NAMES:
            table = metadata.tables['public.' + name]
            assert connection.exec_driver_sql(f'SELECT count(*) FROM public."{name}"').scalar_one() == 0
            pk = inspector.get_pk_constraint(name, schema='public')
            assert (pk['name'], pk['constrained_columns']) == (table.primary_key.name, list(table.primary_key.columns.keys()))
            assert {x['name']: x['column_names'] for x in inspector.get_unique_constraints(name, schema='public')} == {
                c.name: list(c.columns.keys()) for c in table.constraints if isinstance(c, UniqueConstraint)}
            assert {x['name'] for x in inspector.get_check_constraints(name, schema='public')} == {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
            assert inspector.get_table_comment(name, schema='public')['text'] == table.comment
            assert {c['name']: c['comment'] for c in inspector.get_columns(name, schema='public')} == {c.name: c.comment for c in table.c}
            assert not inspector.get_foreign_keys(name, schema='public')
            assert not [i for i in inspector.get_indexes(name, schema='public') if not i.get('duplicates_constraint')]


INVALID = [
    ('review_runs', {'origin_kind': 'unknown'}, 'domain'),
    ('review_runs', {'execution_status': 'completed'}, 'domain'),
    ('review_runs', {'executor_kind': 'background_tasks'}, 'domain'),
    ('review_runs', {'orchestration_mode': 'single_pipeline'}, 'domain'),
    ('review_runs', {'source_snapshot_jsonb': None}, 'domain'),
    ('review_runs', {'origin_kind': 'imported_result'}, 'domain'),
    ('review_runs', {'retry_of_run_id': '@self'}, 'domain'),
    ('review_runs', {'overall_verdict': 'pass'}, 'domain'),
    ('review_runs', {'execution_target': 'guessed'}, 'domain'),
    ('review_attempts', {'attempt_no': 0}, 'domain'),
    ('review_attempts', {'execution_status': 'completed'}, 'domain'),
    ('review_attempts', {'stage': 'completed'}, 'domain'),
    ('review_attempts', {'dispatch_state': 'complete'}, 'domain'),
    ('review_attempts', {'source_attempt_jsonb': None}, 'domain'),
    ('review_attempts', {'worker_token': '@uuid'}, 'domain'),
    ('review_attempts', {'started_at': NOW}, 'domain'),
    ('review_attempts', {'finished_at': LATER}, 'domain'),
    ('review_attempts', {'cost_amount': -1, 'currency': 'USD'}, 'domain'),
    ('review_attempts', {'cost_amount': 'NaN', 'currency': 'USD'}, 'domain'),
    ('review_attempts', {'cost_amount': 0}, 'domain'),
    ('review_attempts', {'currency': 'USD'}, 'domain'),
    ('review_attempts', {'cost_amount': 1, 'currency': 'usd'}, 'domain'),
    ('review_attempts', {'cost_amount': 'Infinity', 'currency': 'USD'}, '22003'),
    ('item_results', {'result_origin': 'unknown'}, 'domain'),
    ('item_results', {'execution_status': 'completed'}, 'domain'),
    ('item_results', {'verdict': 'pass'}, 'domain'),
    ('item_results', {'source_result_jsonb': None}, 'domain'),
    ('run_item_selections', {'selection_kind': 'human_approval'}, 'domain'),
    ('run_item_selections', {'selection_revision': 0}, 'domain'),
    ('run_item_selections', {'selection_revision': 2}, 'domain'),
    ('run_item_selections', {'criteria_item_id': None}, '23502'),
]
for col in ('requested_by','request_operation','idempotency_key','request_hash','request_hash_version','workflow_version',
            'aggregation_policy_version','execution_target','requested_config','input_manifest_hash','manifest_hash_version',
            'retry_policy_jsonb','current_stage','available_at'):
    INVALID.append(('review_runs', {**service_run(), col: None}, 'domain'))
for col in ('request_hash_version','workflow_version','aggregation_policy_version','manifest_hash_version'):
    INVALID.append(('review_runs', {**service_run(), col: ' '}, 'domain'))
for state, columns in {'queued': ('active_attempt_id','selected_attempt_id','finished_at','sealed_at','result_manifest_hash','overall_verdict','error_jsonb'),
                       'running': ('available_at','selected_attempt_id','finished_at','sealed_at','result_manifest_hash','overall_verdict','error_jsonb'),
                       'completed': ('active_attempt_id','available_at','error_jsonb'),
                       'failed': ('active_attempt_id','selected_attempt_id','available_at','sealed_at','result_manifest_hash','overall_verdict')}.items():
    for col in columns:
        value = '@uuid' if col.endswith('_id') else NOW if col.endswith('_at') else 'e'*64 if col.endswith('_hash') else 'pass' if col=='overall_verdict' else {}
        INVALID.append(('review_runs', {**service_run(state), col: value}, 'domain'))
for state, columns in {'running': ('active_attempt_id','started_at'),
                       'completed': ('progress','selected_attempt_id','started_at','finished_at','sealed_at','result_manifest_hash','overall_verdict'),
                       'failed': ('finished_at','error_jsonb')}.items():
    for col in columns:
        INVALID.append(('review_runs', {**service_run(state), col: None}, 'domain'))
for state in ('queued','running','failed'):
    INVALID.append(('review_runs', {**service_run(state), 'progress': 100}, 'domain'))
INVALID.extend(('review_runs', {**service_run(), 'progress': v}, 'domain') for v in (-1, 101))
INVALID.extend([
    ('review_runs', {**service_run(), 'request_operation': 'retry_review'}, 'domain'),
    ('review_runs', {**service_run(), 'retry_of_run_id': '@uuid'}, 'domain'),
    ('review_runs', {**service_run(), 'conflict_policy_version': 'not-supported'}, 'domain'),
    ('review_runs', {**service_run(), 'progress': 32768}, '22003'),
    ('review_runs', {**service_run('completed'), 'progress': 99}, 'domain'),
])
for col in ('current_stage','progress','started_at','finished_at','available_at','active_attempt_id','overall_verdict',
            'request_operation','idempotency_key','request_hash','request_hash_version'):
    value = NOW if col.endswith('_at') else '@uuid' if col.endswith('_id') or col=='idempotency_key' else 1 if col=='progress' else 'f'*64 if col=='request_hash' else 'pass'
    INVALID.append(('review_runs', {col: value}, 'domain'))
for state in ('active','succeeded','failed','unknown'):
    for col in ('worker_token','started_at', *(() if state=='active' else ('finished_at',))):
        INVALID.append(('review_attempts', {**service_attempt(state), col: None}, 'domain'))
INVALID.extend([
    ('review_attempts', {**service_attempt(), 'finished_at': LATER}, 'domain'),
    ('review_attempts', {**service_attempt(), 'lease_expires_at': LATER}, 'domain'),
    ('review_attempts', {**service_attempt(), 'heartbeat_at': NOW}, 'domain'),
    ('review_attempts', {**service_attempt(), 'lease_expires_at': NOW, 'heartbeat_at': LATER}, 'domain'),
    ('review_attempts', {**service_attempt(), 'lease_expires_at': NOW, 'heartbeat_at': NOW}, 'domain'),
])
for state in ('completed','failed','unsupported'):
    for col in ('reason','checker_version','input_manifest_hash'):
        INVALID.append(('item_results', {**service_result(state), col: None}, 'domain'))
    for col in ('reason','checker_version'):
        INVALID.append(('item_results', {**service_result(state), col: ' '}, 'domain'))
INVALID.extend([
    ('item_results', {**service_result(), 'verdict': None}, 'domain'),
    ('item_results', {**service_result(), 'verdict': 'approved'}, 'domain'),
    ('item_results', {**service_result('failed'), 'verdict': 'review'}, 'domain'),
    ('item_results', {**service_result('unsupported'), 'verdict': 'pass'}, 'domain'),
])
for name, columns in {'review_runs': ('request_hash','input_manifest_hash','result_manifest_hash'), 'item_results': ('input_manifest_hash',)}.items():
    for col in columns:
        for value in ('A'*64, 'a'*63):
            patch = service_run('completed') if name=='review_runs' else service_result()
            INVALID.append((name, {**patch, col: value}, 'hashes'))
for name, columns in {'review_runs': ('source_snapshot_jsonb','requested_config','retry_policy_jsonb','error_jsonb'),
                      'review_attempts': ('requested_config','actual_runtime','timing_jsonb','error_jsonb','source_attempt_jsonb'),
                      'item_results': ('error_jsonb','source_result_jsonb')}.items():
    for col in columns:
        INVALID.extend((name, {col: v}, 'json_shapes') for v in ([], 'plain string', '@json_null'))
for col in ('limitations_jsonb','next_action_jsonb'):
    INVALID.extend(('item_results', {col: v}, 'json_shapes') for v in ('plain string', 1, '@json_null'))
for name in ('review_runs','review_attempts','item_results'):
    INVALID.append((name, {'created_at': 'infinity'}, 'times'))
INVALID.extend([
    ('review_runs', {'updated_at': 'infinity'}, 'times'),
    ('review_runs', {'updated_at': '2000-01-01T00:00:00Z'}, 'times'),
    ('review_runs', {**service_run(), 'available_at': 'infinity'}, 'times'),
    ('review_runs', {**service_run('running'), 'started_at': 'infinity'}, 'times'),
    ('review_runs', {**service_run('failed'), 'finished_at': '-infinity'}, 'times'),
    ('review_runs', {**service_run('completed'), 'sealed_at': 'infinity'}, 'times'),
    ('review_runs', {**service_run('completed'), 'started_at': LATER, 'finished_at': NOW}, 'times'),
    ('review_attempts', {**service_attempt(), 'started_at': 'infinity'}, 'times'),
    ('review_attempts', {**service_attempt('failed'), 'finished_at': '-infinity'}, 'times'),
    ('review_attempts', {**service_attempt(), 'lease_expires_at': 'infinity', 'heartbeat_at': NOW}, 'times'),
    ('review_attempts', {**service_attempt(), 'lease_expires_at': LATER, 'heartbeat_at': '-infinity'}, 'times'),
    ('run_item_selections', {'selected_at': 'infinity'}, 'times'),
])


@pytest.mark.parametrize('name,changes,expected', INVALID)
def test_review_checks_reject_invalid_rows(reviews_database, name, changes, expected):
    from sqlalchemy import JSON
    from sqlalchemy.exc import DBAPIError
    _, owner, _, user, project = reviews_database
    body = record(name, project, user)
    for key, value in changes.items():
        if isinstance(value, str) and value.startswith('@'):
            value = {'@self': body[KEYS[name]], '@uuid': uuid4(), '@json_null': JSON.NULL}[value]
        body[key] = value
    with owner_rollback(owner) as connection:
        with pytest.raises(DBAPIError) as caught:
            insert(connection, name, body)
        assert caught.value.orig.sqlstate == (expected if expected.isdigit() else '23514')
        if not expected.isdigit():
            assert caught.value.orig.diag.constraint_name == 'ck_' + name + '_' + expected


@pytest.mark.parametrize('name', REVIEW_NAMES)
def test_primary_keys_are_unique(reviews_database, name):
    from sqlalchemy.exc import IntegrityError
    _, owner, _, user, project = reviews_database
    with owner_rollback(owner) as connection:
        body=record(name,project,user);insert(connection,name,body)
        with pytest.raises(IntegrityError) as caught:insert(connection,name,body)
        assert caught.value.orig.sqlstate=='23505'


@pytest.mark.parametrize('kind', ('run_request','attempt_number','result_attempt_item'))
def test_semantic_unique_keys(reviews_database, kind):
    from sqlalchemy.exc import IntegrityError
    _,owner,_,user,project=reviews_database
    name={'run_request':'review_runs','attempt_number':'review_attempts','result_attempt_item':'item_results'}[kind]
    body=record(name,project,user)
    if kind=='run_request':body.update(service_run())
    duplicate={**body,KEYS[name]:uuid4()}
    if kind=='run_request':duplicate['project_id']=uuid4()
    with owner_rollback(owner) as connection:
        insert(connection,name,body)
        with pytest.raises(IntegrityError) as caught:insert(connection,name,duplicate)
        assert caught.value.orig.sqlstate=='23505'


def test_valid_states_unknown_values_decimal_cost_and_request_scopes(reviews_database):
    from decimal import Decimal
    from backend.app.persistence.schema import metadata
    _,owner,_,user,project=reviews_database
    with owner_rollback(owner) as connection:
        for state in ('queued','running','completed','failed'):
            insert(connection,'review_runs',{**record('review_runs',project,user),**service_run(state)})
        for state in ('active','succeeded','failed','unknown'):
            insert(connection,'review_attempts',{**record('review_attempts',project,user),**service_attempt(state),
                'lease_expires_at':LATER,'heartbeat_at':NOW,'cost_amount':Decimal('123.12345678'),'currency':'USD'})
        for state in ('completed','failed','unsupported'):
            insert(connection,'item_results',{**record('item_results',project,user),**service_result(state)})
        for name in REVIEW_NAMES:
            table=metadata.tables['public.'+name]
            row=connection.execute(table.insert().values(**record(name,project,user)).returning(table)).mappings().one()
            if name=='review_runs':assert row['overall_verdict'] is None and row['started_at'] is None
            if name=='review_attempts':assert row['retryable'] is None and row['cost_amount'] is None and row['worker_token'] is None
            if name=='item_results':assert row['verdict'] is None and row['observed_value'] is None and row['checker_version'] is None
            if name=='run_item_selections':assert row['selection_revision']==1
        values=connection.exec_driver_sql('SELECT cost_amount FROM public.review_attempts WHERE cost_amount IS NOT NULL').scalars().all()
        assert values==[Decimal('123.12345678')]*4
        # Create and retry are distinct operations; request idempotency is not image uniqueness.
        body={**record('review_runs',project,user),**service_run()}
        insert(connection,'review_runs',body)
        insert(connection,'review_runs',{**body,'run_id':uuid4(),'retry_of_run_id':body['run_id'],'request_operation':'retry_review'})
        insert(connection,'review_runs',{**body,'run_id':uuid4(),'idempotency_key':uuid4()})


def test_app_permissions_repeat_and_downgrade_guard(reviews_database):
    from alembic import command
    from alembic.util import CommandError
    from sqlalchemy import text
    db,owner,configuration,_,_=reviews_database
    command.upgrade(configuration(),'M005')
    with pytest.raises(CommandError,match='M005 destructive downgrade is disabled'):
        command.downgrade(configuration(),'M004')
    with owner.connect() as connection:
        for name in REVIEW_NAMES:
            assert not connection.execute(text("SELECT has_table_privilege(:app,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),{'app':db.settings.database.username,'table':'public.'+name}).scalar_one()
