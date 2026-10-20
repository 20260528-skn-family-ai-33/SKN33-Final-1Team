"""M002 -> M003 on isolated PostgreSQL; preserve originals and uncertainty."""
from datetime import date, datetime, timezone
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_identity_migration_integration import migrated, owner_rollback  # noqa: F401
from backend.tests.test_file_migration_integration import files_database, FILE_NAMES, record as file_record, insert  # noqa: F401

pytestmark = pytest.mark.db_integration
OCR_NAMES = ('schema_definitions', 'import_packages', 'audit_events', 'ocr_runs', 'ocr_tokens',
             'poster_extractions', 'poster_field_observations', 'evidence_records', 'evidence_ocr_tokens')
KEYS = dict(zip(OCR_NAMES, ('schema_id', 'import_id', 'event_id', 'ocr_run_id', 'token_id',
                          'extraction_id', 'observation_id', 'evidence_id', 'evidence_id')))
NOW = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc)


@pytest.fixture(scope='module')
def ocr_database(files_database, tmp_path_factory):
    from alembic import command
    from alembic.util import CommandError
    from sqlalchemy import text
    db, owner, configuration, user, project = files_database
    prior = ('users', 'user_sessions', 'projects', 'project_members', *FILE_NAMES)
    with owner.begin() as connection:
        for name in FILE_NAMES:
            insert(connection, name, file_record(name, user, project))
    def snapshot():
        with owner.connect() as connection:
            return (connection.exec_driver_sql("SELECT c.relname,c.oid::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY 1").all(),
                    connection.exec_driver_sql('SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2').all(),
                    [connection.exec_driver_sql(f'SELECT count(*) FROM public."{name}"').scalar_one() for name in prior])
    before = snapshot()
    with owner.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE public.evidence_records (marker text)')
        connection.exec_driver_sql("INSERT INTO public.evidence_records VALUES ('preserve preexisting evidence')")
    with pytest.raises(CommandError, match='SQLSTATE=42P07'):
        command.upgrade(configuration(), 'M003')
    with owner.begin() as connection:
        assert connection.exec_driver_sql('SELECT marker FROM public.evidence_records').scalar_one() == 'preserve preexisting evidence'
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one() == 'M002'
        assert connection.exec_driver_sql("SELECT to_regclass('public.schema_definitions') IS NULL").scalar_one()
        connection.exec_driver_sql('DROP TABLE public.evidence_records')
    assert snapshot() == before
    directory = tmp_path_factory.mktemp('c04_failed_revision') / 'migrations'
    shutil.copytree('backend/migrations', directory, ignore=shutil.ignore_patterns('__pycache__'))
    revision = directory / 'versions/M003_ocr.py'
    revision.write_text(revision.read_text().replace('\n\ndef downgrade():', '\n    op.execute(text("SELECT 1 / 0"))\n\ndef downgrade():'))
    with pytest.raises(CommandError, match='SQLSTATE=22012'):
        command.upgrade(configuration(directory), 'M003')
    assert snapshot() == before
    command.upgrade(configuration(), 'M003')
    with owner.connect() as connection:
        assert connection.execute(text('SELECT name FROM public.projects WHERE project_id=:id'), {'id': project}).scalar_one() == 'C03 preserved fixture'
    yield db, owner, configuration, user, project


def record(name, project):
    body = {KEYS[name]: uuid4()}
    if name != 'schema_definitions':
        body['project_id'] = project
    if name == 'schema_definitions':
        return {**body, 'schema_name': 'fixture-only', 'schema_version': '1'}
    if name == 'import_packages':
        return {**body, 'source_namespace': 'fixture-intake'}
    if name == 'audit_events':
        return {**body, 'actor_kind': 'developer', 'action': 'fixture.import', 'target_type': 'fixture'}
    if name == 'ocr_runs':
        return {**body, 'asset_version_id': uuid4(), 'import_package_id': uuid4(), 'source_file_id': uuid4(),
                'frame_id': uuid4(), 'execution_status': 'imported', 'coordinate_space': 'normalized_original_pixels'}
    if name == 'ocr_tokens':
        return {**body, 'asset_version_id': uuid4(), 'ocr_run_id': uuid4(), 'external_token_id': '1',
                'external_token_type': 'string', 'token_order': 0, 'text': '원문', 'source_record_jsonb': {'id': '1', 'text': '원문'}}
    if name == 'poster_extractions':
        return {**body, 'asset_version_id': uuid4(), 'version_no': 1, 'method': 'fixture-import',
                'payload_jsonb': {'date': None}, 'compatibility_state': 'pending_enrichment', 'projection_version': 'fixture-v1'}
    if name == 'poster_field_observations':
        return {**body, 'asset_version_id': uuid4(), 'extraction_id': uuid4(), 'field_code': 'date',
                'field_path': '/fields/date', 'adapter_version': 'fixture-v1'}
    if name == 'evidence_records':
        return {**body, 'asset_version_id': uuid4(), 'observation_id': uuid4(), 'source_kind': 'ocr',
                'source_ocr_run_id': uuid4(), 'method_code': 'fixture-source-token'}
    return {**body, 'token_id': uuid4(), 'asset_version_id': uuid4(), 'ocr_run_id': uuid4(), 'token_order': 0}


def test_full_inventory_and_prior_data_preserved(ocr_database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import CheckConstraint, UniqueConstraint, inspect
    from backend.tests.schema_helpers import metadata_before_relations
    metadata = metadata_before_relations(['users', 'user_sessions', 'projects', 'project_members', 'assets', 'asset_versions', 'stored_files', 'image_coordinate_frames', 'record_aliases', 'schema_definitions', 'import_packages', 'audit_events', 'ocr_runs', 'ocr_tokens', 'poster_extractions', 'poster_field_observations', 'evidence_records', 'evidence_ocr_tokens'])
    _, owner, _, _, _ = ocr_database
    with owner.connect() as connection:
        inspector = inspect(connection)
        assert set(inspector.get_table_names(schema='public')) == {t.name for t in metadata.tables.values()} | {'alembic_version'}
        assert len(metadata.tables) == 18
        assert sum(len(inspector.get_columns(t.name, schema='public')) for t in metadata.tables.values()) == 236
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one() == 'M003'
        differences = compare_metadata(MigrationContext.configure(connection, opts={'include_schemas': True, 'compare_server_default': True,
            'include_object': lambda obj, name, kind, reflected, compare_to: getattr(obj, 'schema', 'public') in ('public', None) and (kind != 'table' or name != 'alembic_version')}), metadata)
        assert differences == [], differences
        for name in OCR_NAMES:
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
        for name in FILE_NAMES:
            assert connection.exec_driver_sql(f'SELECT count(*) FROM public."{name}"').scalar_one() == 1


INVALID = [
    ('schema_definitions', {'schema_name': ' \t'}, 'domain'),
    ('schema_definitions', {'schema_version': ''}, 'domain'),
    ('schema_definitions', {'availability_state': 'unknown'}, 'domain'),
    ('schema_definitions', {'access_scope': 'public'}, 'domain'),
    ('schema_definitions', {'availability_state': 'available'}, 'domain'),
    ('schema_definitions', {'schema_sha256': 'a'*64}, 'domain'),
    ('schema_definitions', {'access_scope': 'service_shared'}, 'domain'),
    ('schema_definitions', {'validation_schema_file_id': '@uuid', 'schema_sha256': 'A'*64}, 'hashes'),
    ('import_packages', {'source_namespace': ' '}, 'domain'),
    ('import_packages', {'hash_check_state': 'verified'}, 'domain'),
    ('import_packages', {'import_state': 'approved'}, 'domain'),
    ('import_packages', {'package_fingerprint': 'b'*64}, 'domain'),
    ('import_packages', {'fingerprint_version': 'v1'}, 'domain'),
    ('import_packages', {'package_fingerprint': 'b'*64, 'fingerprint_version': ' '}, 'domain'),
    ('import_packages', {'package_fingerprint': 'B'*64, 'fingerprint_version': 'v1'}, 'hashes'),
    ('import_packages', {'replaces_import_id': '@self'}, 'domain'),
    ('import_packages', {'import_state': 'completed'}, 'domain'),
    ('audit_events', {'actor_kind': 'user'}, 'domain'),
    ('audit_events', {'actor_kind': 'system', 'actor_user_id': '@uuid'}, 'domain'),
    ('audit_events', {'actor_kind': 'unknown'}, 'domain'),
    ('audit_events', {'action': ' '}, 'domain'),
    ('audit_events', {'target_type': ''}, 'domain'),
    ('ocr_runs', {'execution_status': 'approved'}, 'domain'),
    ('ocr_runs', {'import_package_id': None}, 'domain'),
    ('ocr_runs', {'coordinate_space': ' '}, 'domain'),
    ('ocr_runs', {'execution_target': 'guessed'}, 'domain'),
    ('ocr_runs', {'execution_status': 'queued', 'sealed_at': NOW}, 'domain'),
    ('ocr_runs', {'execution_status': 'running', 'sealed_at': NOW}, 'domain'),
    ('ocr_runs', {'source_file_id': None}, 'domain'),
    ('ocr_runs', {'started_at': NOW}, 'domain'),
    ('ocr_runs', {'completed_at': LATER}, 'domain'),
    ('ocr_runs', {'execution_status': 'completed', 'import_package_id': None}, 'domain'),
    ('ocr_runs', {'execution_status': 'failed'}, 'domain'),
    ('ocr_runs', {'execution_status': 'failed', 'completed_at': LATER}, 'domain'),
    ('ocr_runs', {'execution_status': 'failed', 'error_jsonb': {}}, 'domain'),
    ('ocr_runs', {'execution_status': 'failed', 'completed_at': NOW, 'started_at': LATER, 'error_jsonb': {}}, 'times'),
    ('ocr_tokens', {'external_token_type': 'float'}, 'domain'),
    ('ocr_tokens', {'external_token_id': ' '}, 'domain'),
    ('ocr_tokens', {'token_order': -1}, 'domain'),
    ('poster_extractions', {'version_no': 0}, 'domain'),
    ('poster_extractions', {'method': ' '}, 'domain'),
    ('poster_extractions', {'projection_version': ''}, 'domain'),
    ('poster_extractions', {'compatibility_state': 'approved'}, 'domain'),
    ('poster_extractions', {'validation_state': 'guessed'}, 'domain'),
    ('poster_extractions', {'validation_state': 'valid'}, 'domain'),
    ('poster_extractions', {'validation_state': 'valid', 'schema_id': '@uuid'}, 'domain'),
    ('poster_extractions', {'predecessor_id': '@self'}, 'domain'),
    ('poster_extractions', {'start_date': date(2026, 10, 17)}, 'domain'),
    ('poster_extractions', {'start_year': 2026, 'start_month': 10, 'start_day': 16, 'start_date': date(2026, 10, 17)}, 'domain'),
    ('poster_field_observations', {'field_code': ' '}, 'domain'),
    ('poster_field_observations', {'field_path': ''}, 'domain'),
    ('poster_field_observations', {'adapter_version': '\t'}, 'domain'),
    ('poster_field_observations', {'presence_status': 'unknown'}, 'domain'),
    ('poster_field_observations', {'resolution_status': 'approved'}, 'domain'),
    ('poster_field_observations', {'resolution_status': 'resolved'}, 'domain'),
    ('poster_field_observations', {'resolution_status': 'resolved', 'presence_status': 'present', 'normalized_value': '@json_null'}, 'domain'),
    ('poster_field_observations', {'presence_status': 'absent', 'normalized_value': []}, 'domain'),
    ('poster_field_observations', {'presence_status': 'absent', 'normalized_value': '@json_null'}, 'domain'),
    ('evidence_records', {'observation_id': None}, 'domain'),
    ('evidence_records', {'item_result_id': '@uuid'}, 'domain'),
    ('evidence_records', {'source_kind': 'unknown'}, 'domain'),
    ('evidence_records', {'method_code': ''}, 'domain'),
    ('evidence_records', {'source_ocr_run_id': None}, 'domain'),
    ('evidence_records', {'source_kind': 'image'}, 'domain'),
    ('evidence_records', {'source_kind': 'image', 'source_file_id': '@uuid'}, 'domain'),
    ('evidence_records', {'source_kind': 'document'}, 'domain'),
    ('evidence_records', {'source_kind': 'document', 'source_file_id': '@uuid'}, 'domain'),
    ('evidence_records', {'source_kind': 'document', 'source_file_id': '@uuid', 'field_pointer': ' ', 'locator_jsonb': {}}, 'domain'),
    ('evidence_records', {'page_no': 0}, 'domain'),
    ('evidence_records', {'bbox_xyxy': [0, 0, 1, 1]}, 'domain'),
    ('evidence_ocr_tokens', {'token_order': -1}, 'domain'),
    ('evidence_ocr_tokens', {'token_id': None}, '23502'),
]
for name, cols in {
    'schema_definitions': ('created_at',), 'import_packages': ('imported_at',), 'audit_events': ('occurred_at',),
    'ocr_runs': ('created_at', 'sealed_at'), 'ocr_tokens': ('created_at',),
    'poster_extractions': ('created_at', 'sealed_at'), 'poster_field_observations': ('created_at',),
    'evidence_records': ('created_at',), 'evidence_ocr_tokens': ('created_at',),
}.items():
    INVALID.extend((name, {col: 'infinity'}, 'times') for col in cols)
for name, cols in {
    'schema_definitions': ('field_catalog_jsonb',),
    'import_packages': ('reported_validation_jsonb', 'local_validation_jsonb', 'provenance_jsonb'),
    'audit_events': ('event_metadata',), 'ocr_runs': ('engine_settings', 'runtime_metadata', 'error_jsonb'),
    'ocr_tokens': ('source_record_jsonb',), 'poster_extractions': ('payload_jsonb', 'checks_jsonb', 'provenance_jsonb'),
    'poster_field_observations': ('normalization_jsonb',), 'evidence_records': ('locator_jsonb',),
}.items():
    for col in cols:
        INVALID.extend((name, {col: value}, 'json_shapes') for value in ([], 1, '@json_null'))
for name in ('ocr_tokens', 'evidence_records'):
    INVALID.extend((name, {'confidence': value}, 'domain') for value in (-0.01, 1.01, float('nan'), float('inf'), float('-inf')))
    for value in ({}, [], [0, 0, 1], [0, 0, 1, 1, 2], [0, 0, '1', 1], [0, False, 1, 1],
                  [None, 0, 1, 1], [0, 0, 0, 1], [-1, 0, 1, 1], [0, 2, 1, 1], '@json_null'):
        INVALID.append((name, {'bbox_xyxy': value, **({'frame_id': '@uuid'} if name == 'evidence_records' else {})}, 'json_shapes'))
    for value in ({}, [], [[0, 0], [1, 1]], [[0, 0], [1, 1], 'bad'], [[0, 0], [1], [2, 2]],
                  [[0, 0], [1, 1], [2, 2, 3]], [[0, 0], [1, 1], [True, 2]], [[0, 0], [1, 1], [None, 2]],
                  [[0, 0], [1, 1], [[2], 2]], '@json_null'):
        INVALID.append((name, {'polygon': value, **({'frame_id': '@uuid'} if name == 'evidence_records' else {})}, 'json_shapes'))
for value in ('01', '+1', '-0', '1.0', '1e2', ' 1', '1 ', '١', '-', '--1'):
    INVALID.append(('ocr_tokens', {'external_token_type': 'integer', 'external_token_id': value}, 'domain'))
for col in ('year_value', 'start_year', 'end_year', 'start_month', 'end_month', 'start_day', 'end_day'):
    upper = 9999 if 'year' in col else 12 if 'month' in col else 31
    INVALID.extend(('poster_extractions', {col: value}, 'domain') for value in (0, upper + 1))
INVALID.extend([
    ('evidence_records', {'limitations_jsonb': 'plain string'}, 'json_shapes'),
    ('evidence_records', {'limitations_jsonb': '@json_null'}, 'json_shapes'),
    ('ocr_tokens', {'source_record_jsonb': None}, '23502'),
    ('poster_extractions', {'payload_jsonb': None}, '23502'),
    ('ocr_tokens', {'external_token_id': 'x'*151}, '22001'),
    ('import_packages', {'source_namespace': 'x'*101}, '22001'),
])

# Other required values remain valid so each conditional NULL hole is tested alone.
for col in ('manifest_file_id', 'package_fingerprint', 'fingerprint_version', 'local_validation_jsonb'):
    body = {'manifest_file_id': '@uuid', 'package_fingerprint': 'a'*64, 'fingerprint_version': 'fixture-v1',
            'hash_check_state': 'matched', 'import_state': 'completed', 'local_validation_jsonb': {}}
    body[col] = None
    INVALID.append(('import_packages', body, 'domain'))
for state in ('not_checked', 'mismatch'):
    INVALID.append(('import_packages', {'manifest_file_id': '@uuid', 'package_fingerprint': 'a'*64,
        'fingerprint_version': 'fixture-v1', 'hash_check_state': state, 'import_state': 'completed', 'local_validation_jsonb': {}}, 'domain'))
for col in ('attempt_id', 'input_file_id', 'source_file_id', 'started_at', 'completed_at'):
    body = {'execution_status': 'completed', 'attempt_id': '@uuid', 'input_file_id': '@uuid',
            'source_file_id': '@uuid', 'started_at': NOW, 'completed_at': LATER}
    body[col] = None
    INVALID.append(('ocr_runs', body, 'domain'))
for col in ('validation_schema_file_id', 'schema_sha256'):
    body = {'availability_state': 'available', 'validation_schema_file_id': '@uuid', 'schema_sha256': 'a'*64}
    body[col] = None
    INVALID.append(('schema_definitions', body, 'domain'))
INVALID.extend([
    ('poster_extractions', {'validation_state': 'valid', 'checks_jsonb': {}}, 'domain'),
    ('poster_extractions', {'end_date': date(2026, 10, 17)}, 'domain'),
    ('poster_extractions', {'end_year': 2026, 'end_month': 10, 'end_day': 16, 'end_date': date(2026, 10, 17)}, 'domain'),
    ('poster_extractions', {'start_year': 2026, 'start_month': 10, 'start_day': 17, 'start_date': 'infinity'}, 'domain'),
    ('poster_extractions', {'end_year': 2026, 'end_month': 10, 'end_day': 17, 'end_date': '-infinity'}, 'domain'),
    ('ocr_runs', {'execution_status': 'failed', 'completed_at': 'infinity', 'error_jsonb': {}}, 'times'),
    ('ocr_runs', {'execution_status': 'failed', 'started_at': '-infinity', 'completed_at': LATER, 'error_jsonb': {}}, 'times'),
])


@pytest.mark.parametrize('name,changes,expected', INVALID)
def test_ocr_checks_reject_invalid_rows(ocr_database, name, changes, expected):
    from sqlalchemy import JSON
    from sqlalchemy.exc import DBAPIError
    _, owner, _, _, project = ocr_database
    body = record(name, project)
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


@pytest.mark.parametrize('name', OCR_NAMES)
def test_primary_keys_reject_duplicates(ocr_database, name):
    from sqlalchemy.exc import IntegrityError
    _, owner, _, _, project = ocr_database
    with owner_rollback(owner) as connection:
        body = record(name, project)
        insert(connection, name, body)
        with pytest.raises(IntegrityError) as caught:
            insert(connection, name, body)
        assert caught.value.orig.sqlstate == '23505'


@pytest.mark.parametrize('name,duplicate_columns', [
    ('schema_definitions', ('schema_name', 'schema_version')),
    ('import_packages', ('project_id', 'source_namespace', 'package_fingerprint')),
    ('ocr_tokens', ('ocr_run_id', 'external_token_type', 'external_token_id')),
    ('ocr_tokens', ('ocr_run_id', 'token_order')),
    ('poster_extractions', ('asset_version_id', 'version_no')),
    ('poster_field_observations', ('extraction_id', 'field_path')),
    ('evidence_ocr_tokens', ('evidence_id', 'token_order')),
])
def test_semantic_unique_keys_reject_duplicates(ocr_database, name, duplicate_columns):
    from sqlalchemy.exc import IntegrityError
    _, owner, _, _, project = ocr_database
    body, duplicate = record(name, project), record(name, project)
    if name == 'import_packages':
        body.update(package_fingerprint='c'*64, fingerprint_version='fixture-v1')
        duplicate['fingerprint_version'] = 'fixture-v1'
    for col in duplicate_columns:
        duplicate[col] = body[col]
    with owner_rollback(owner) as connection:
        insert(connection, name, body)
        with pytest.raises(IntegrityError) as caught:
            insert(connection, name, duplicate)
        assert caught.value.orig.sqlstate == '23505'


def test_uncertainty_defaults_and_source_values_are_not_fabricated(ocr_database):
    from sqlalchemy import select
    from backend.app.persistence.schema import metadata
    _, owner, _, _, project = ocr_database
    with owner_rollback(owner) as connection:
        for name in OCR_NAMES:
            body = record(name, project)
            row = connection.execute(metadata.tables['public.' + name].insert().values(**body).returning(metadata.tables['public.' + name])).mappings().one()
            if name == 'schema_definitions':
                assert (row['availability_state'], row['access_scope'], row['schema_sha256']) == ('metadata_only', 'metadata_only', None)
            if name == 'import_packages':
                assert (row['hash_check_state'], row['import_state'], row['package_fingerprint']) == ('not_checked', 'received', None)
            if name == 'ocr_runs':
                assert all(row[c] is None for c in ('engine_version', 'model_name', 'device', 'started_at', 'completed_at', 'sealed_at', 'schema_id'))
            if name == 'ocr_tokens':
                assert row['confidence'] is None and row['requires_review'] is None and row['bbox_xyxy'] is None
            if name == 'poster_extractions':
                assert row['start_date'] is None and row['year_value'] is None and row['validation_state'] == 'unchecked'
            if name == 'poster_field_observations':
                assert (row['presence_status'], row['resolution_status'], row['normalized_value']) == ('not_checked', 'unprocessed', None)
        tokens = metadata.tables['public.ocr_tokens']
        base = record('ocr_tokens', project)
        base.update(external_token_id='1', external_token_type='integer', token_order=0, confidence=0.500000049,
                    bbox_xyxy=[0, 0.25, 100.5, 200], polygon=[[0, 0], [100.5, 0.25], [50, 200]])
        for i, value in enumerate(('1', '0', '-1', '9'*150)):
            body = {**base, 'token_id': uuid4(), 'external_token_id': value, 'token_order': i}
            insert(connection, 'ocr_tokens', body)
        insert(connection, 'ocr_tokens', {**base, 'token_id': uuid4(), 'external_token_type': 'string', 'token_order': 4})
        actual = connection.execute(select(tokens.c.confidence).where(tokens.c.ocr_run_id == base['ocr_run_id'])).scalars().all()
        assert actual == [0.500000049]*5  # DOUBLE PRECISION is not rounded to six decimals


def test_partial_dates_and_contradictory_ranges_preserve_original(ocr_database):
    _, owner, _, _, project = ocr_database
    with owner_rollback(owner) as connection:
        for changes in ({'start_month': 2, 'start_day': 30},  # invalid full date stays partial
                        {'start_year': 2024, 'start_month': 2, 'start_day': 29, 'start_date': date(2024, 2, 29)},
                        {'start_year': 2026, 'start_month': 10, 'start_day': 17, 'start_date': date(2026, 10, 17),
                         'end_year': 2026, 'end_month': 10, 'end_day': 16, 'end_date': date(2026, 10, 16)}):
            insert(connection, 'poster_extractions', {**record('poster_extractions', project), **changes})


def test_json_null_variable_values_and_real_source_kinds(ocr_database):
    from sqlalchemy import JSON, select
    from backend.app.persistence.schema import metadata
    _, owner, _, _, project = ocr_database
    with owner_rollback(owner) as connection:
        observations = metadata.tables['public.poster_field_observations']
        absent = {**record('poster_field_observations', project), 'presence_status': 'absent', 'normalized_value': None}
        insert(connection, 'poster_field_observations', absent)
        unknown = {**record('poster_field_observations', project), 'normalized_value': JSON.NULL}
        insert(connection, 'poster_field_observations', unknown)
        assert connection.execute(select(observations.c.normalized_value.is_(None)).where(observations.c.observation_id == absent['observation_id'])).scalar_one()
        assert not connection.execute(select(observations.c.normalized_value.is_(None)).where(observations.c.observation_id == unknown['observation_id'])).scalar_one()
        for value in (False, 0, '', [], {}, '원문'):
            insert(connection, 'poster_field_observations', {**record('poster_field_observations', project),
                'presence_status': 'present', 'resolution_status': 'resolved', 'normalized_value': value})
        for changes in ({'source_kind': 'image', 'source_file_id': uuid4(), 'frame_id': uuid4()},
                        {'source_kind': 'document', 'source_file_id': uuid4(), 'page_no': 1},
                        {'source_kind': 'document', 'source_file_id': uuid4(), 'field_pointer': '/pages/0/title'},
                        {'source_kind': 'document', 'source_file_id': uuid4(), 'locator_jsonb': {'section': 'title'}},
                        {'item_result_id': uuid4(), 'observation_id': None, 'limitations_jsonb': ['미확인']},
                        {'frame_id': uuid4(), 'bbox_xyxy': [0, 0, 1, 1], 'polygon': [[0, 0], [1, 0], [1, 1]], 'confidence': 1.0}):
            insert(connection, 'evidence_records', {**record('evidence_records', project), **changes})


def test_complete_rows_do_not_require_guessed_schema_or_versions(ocr_database):
    _, owner, _, _, project = ocr_database
    with owner_rollback(owner) as connection:
        insert(connection, 'schema_definitions', {**record('schema_definitions', project), 'availability_state': 'available',
               'access_scope': 'service_shared', 'validation_schema_file_id': uuid4(), 'schema_sha256': 'd'*64})
        insert(connection, 'import_packages', {**record('import_packages', project), 'manifest_file_id': uuid4(),
               'package_fingerprint': 'e'*64, 'fingerprint_version': 'fixture-v1', 'hash_check_state': 'matched',
               'import_state': 'completed', 'local_validation_jsonb': {'checked': ['fixture']}})
        base = record('ocr_runs', project)
        insert(connection, 'ocr_runs', {**base, 'execution_status': 'completed', 'attempt_id': uuid4(),
               'input_file_id': uuid4(), 'started_at': NOW, 'completed_at': LATER})
        insert(connection, 'ocr_runs', {**base, 'ocr_run_id': uuid4(), 'execution_status': 'failed',
               'source_file_id': None, 'completed_at': LATER, 'error_jsonb': {'code': 'fixture-failed'}})
        insert(connection, 'poster_extractions', {**record('poster_extractions', project), 'validation_state': 'valid',
               'schema_id': uuid4(), 'checks_jsonb': {'method': 'fixture-schema'}})
        for actor in ({'actor_kind': 'user', 'actor_user_id': uuid4()}, {'actor_kind': 'system'}, {'actor_kind': 'developer', 'actor_user_id': uuid4()}):
            insert(connection, 'audit_events', {**record('audit_events', project), **actor})


def test_null_import_fingerprints_do_not_merge_and_contexts_stay_distinct(ocr_database):
    _, owner, _, _, project = ocr_database
    with owner_rollback(owner) as connection:
        base = record('import_packages', project)
        insert(connection, 'import_packages', base)
        insert(connection, 'import_packages', {**base, 'import_id': uuid4()})
        for changes in ({}, {'project_id': uuid4()}, {'source_namespace': 'other namespace'}):
            insert(connection, 'import_packages', {**base, 'import_id': uuid4(), 'package_fingerprint': 'f'*64,
                   'fingerprint_version': 'fixture-v1', **changes})


def test_app_permissions_repeat_upgrade_and_downgrade_guard(ocr_database):
    from alembic import command
    from alembic.util import CommandError
    from sqlalchemy import text
    _, owner, configuration, _, _ = ocr_database
    command.upgrade(configuration(), 'M003')
    with pytest.raises(CommandError, match='M003 destructive downgrade is disabled'):
        command.downgrade(configuration(), 'M002')
    db = ocr_database[0]
    with owner.connect() as connection:
        for name in (*OCR_NAMES, 'alembic_version'):
            assert not connection.execute(text("SELECT has_table_privilege(:app,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),
                {'app': db.settings.database.username, 'table': 'public.' + name}).scalar_one()
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one() == 'M003'
    with db.engine.connect() as connection:
        from sqlalchemy.exc import DBAPIError
        with pytest.raises(DBAPIError) as caught:
            connection.exec_driver_sql('SELECT * FROM public.ocr_runs')
        assert caught.value.orig.sqlstate == '42501'
