"""M003 -> M004 on isolated PostgreSQL; no service confirmation or file writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import shutil
import threading
from uuid import uuid4

import pytest

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_identity_migration_integration import migrated, owner_rollback  # noqa: F401
from backend.tests.test_file_migration_integration import files_database, FILE_NAMES, insert  # noqa: F401
from backend.tests.test_ocr_migration_integration import ocr_database, OCR_NAMES, record as ocr_record  # noqa: F401

pytestmark = pytest.mark.db_integration
CRITERIA_NAMES = ('criteria_sets', 'criteria_drafts', 'criteria_versions', 'criteria_items')
KEYS = dict(zip(CRITERIA_NAMES, ('criteria_set_id', 'draft_id', 'criteria_version_id', 'criteria_item_id')))
NOW = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope='module')
def criteria_database(ocr_database, tmp_path_factory):
    from alembic import command
    from alembic.util import CommandError
    db, owner, configuration, user, project = ocr_database
    prior = ('users', 'user_sessions', 'projects', 'project_members', *FILE_NAMES, *OCR_NAMES)
    with owner.begin() as connection:
        for name in OCR_NAMES:
            insert(connection, name, ocr_record(name, project))
    def snapshot():
        with owner.connect() as connection:
            return (connection.exec_driver_sql("SELECT c.relname,c.oid::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY 1").all(),
                    connection.exec_driver_sql('SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2').all(),
                    [connection.exec_driver_sql(f'SELECT to_jsonb(t)::text FROM public."{name}" t ORDER BY to_jsonb(t)::text').all() for name in prior])
    before = snapshot()
    with owner.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE public.criteria_versions (marker text)')
        connection.exec_driver_sql("INSERT INTO public.criteria_versions VALUES ('preserve prior object')")
    with pytest.raises(CommandError, match='SQLSTATE=42P07'):
        command.upgrade(configuration(), 'M004')
    with owner.begin() as connection:
        assert connection.exec_driver_sql('SELECT marker FROM public.criteria_versions').scalar_one() == 'preserve prior object'
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one() == 'M003'
        assert connection.exec_driver_sql("SELECT to_regclass('public.criteria_sets') IS NULL AND to_regclass('public.criteria_drafts') IS NULL").scalar_one()
        connection.exec_driver_sql('DROP TABLE public.criteria_versions')
    assert snapshot() == before
    directory = tmp_path_factory.mktemp('c05_failed_revision') / 'migrations'
    shutil.copytree('backend/migrations', directory, ignore=shutil.ignore_patterns('__pycache__'))
    revision = directory / 'versions/M004_criteria.py'
    revision.write_text(revision.read_text().replace('\n\ndef downgrade():', '\n    op.execute(text("SELECT 1 / 0"))\n\ndef downgrade():'))
    with pytest.raises(CommandError, match='SQLSTATE=22012'):
        command.upgrade(configuration(directory), 'M004')
    assert snapshot() == before
    command.upgrade(configuration(), 'M004')
    assert snapshot()[2] == before[2]
    yield db, owner, configuration, user, project


def record(name, project, user):
    body = {KEYS[name]: uuid4(), 'project_id': project}
    if name == 'criteria_sets':
        return {**body, 'name': '같은 표시명'}
    if name == 'criteria_drafts':
        return {**body, 'criteria_set_id': uuid4(), 'target_asset_version_id': uuid4(),
                'raw_requirements': '', 'items_jsonb': [], 'updated_by': user}
    if name == 'criteria_versions':
        return {**body, 'criteria_set_id': uuid4(), 'target_asset_version_id': uuid4(), 'version_no': 1,
                'criteria_hash': 'a'*64, 'hash_version': 'fixture-import-projection-v1',
                'origin_kind': 'legacy_snapshot', 'confirmation_state': 'unconfirmed',
                'source_snapshot_jsonb': {'criteria_version': 1, 'items': []}, 'source_version_jsonb': 1}
    return {**body, 'target_asset_version_id': uuid4(), 'criteria_version_id': uuid4(), 'position': 1,
            'category': 'information', 'text': '확인된 기준 문구'}


def service_values():
    return {'origin_kind': 'service_confirmation', 'confirmation_state': 'confirmed', 'source_draft_id': uuid4(),
            'source_draft_revision': 1, 'source_draft_hash': 'b'*64, 'source_requirements_text': '확정 요청 원문',
            'confirmed_by': uuid4(), 'confirmed_at': NOW, 'confirmation_key': uuid4(),
            'confirmation_request_hash': 'c'*64, 'request_hash_version': 'fixture-request-v1'}


def test_full_inventory_defaults_comments_and_previous_rows_preserved(criteria_database):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import CheckConstraint, UniqueConstraint, inspect
    from backend.tests.schema_helpers import metadata_before_relations
    metadata = metadata_before_relations(['users', 'user_sessions', 'projects', 'project_members', 'assets', 'asset_versions', 'stored_files', 'image_coordinate_frames', 'record_aliases', 'schema_definitions', 'import_packages', 'audit_events', 'ocr_runs', 'ocr_tokens', 'poster_extractions', 'poster_field_observations', 'evidence_records', 'evidence_ocr_tokens', 'criteria_sets', 'criteria_drafts', 'criteria_versions', 'criteria_items'])
    _, owner, _, _, _ = criteria_database
    with owner.connect() as connection:
        inspector = inspect(connection)
        assert set(inspector.get_table_names(schema='public')) == {t.name for t in metadata.tables.values()} | {'alembic_version'}
        assert len(metadata.tables) == 22
        assert sum(len(inspector.get_columns(t.name, schema='public')) for t in metadata.tables.values()) == 299
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one() == 'M004'
        diff = compare_metadata(MigrationContext.configure(connection, opts={'include_schemas': True, 'compare_server_default': True,
            'include_object': lambda obj, name, kind, reflected, compare_to: getattr(obj, 'schema', 'public') in ('public', None) and (kind != 'table' or name != 'alembic_version')}), metadata)
        assert diff == [], diff
        for name in CRITERIA_NAMES:
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
        for name in (*FILE_NAMES, *OCR_NAMES):
            assert connection.exec_driver_sql(f'SELECT count(*) FROM public."{name}"').scalar_one() == 1


INVALID = [
    ('criteria_sets', {'name': ' \t'}, 'domain'),
    ('criteria_sets', {'name': None}, '23502'),
    ('criteria_sets', {'name': 'x'*201}, '22001'),
    ('criteria_drafts', {'revision': 0}, 'domain'),
    ('criteria_drafts', {'revision': -1}, 'domain'),
    ('criteria_drafts', {'revision': 2**63}, '22003'),
    ('criteria_drafts', {'state': 'confirmed'}, 'domain'),
    ('criteria_drafts', {'items_jsonb': None}, '23502'),
    ('criteria_drafts', {'raw_requirements': None}, '23502'),
    ('criteria_versions', {'version_no': 0}, 'domain'),
    ('criteria_versions', {'source_draft_revision': 0}, 'domain'),
    ('criteria_versions', {'source_draft_revision': 2**63}, '22003'),
    ('criteria_versions', {'hash_version': ' '}, 'domain'),
    ('criteria_versions', {'origin_kind': 'unknown'}, 'domain'),
    ('criteria_versions', {'confirmation_state': 'approved'}, 'domain'),
    ('criteria_versions', {'source_criteria_version_id': '@self'}, 'domain'),
    ('criteria_versions', {'source_snapshot_jsonb': None}, 'domain'),
    ('criteria_versions', {'source_version_jsonb': None}, 'domain'),
    ('criteria_versions', {'criteria_hash': None}, '23502'),
    ('criteria_versions', {'criteria_hash': 'x'*65}, '22001'),
    ('criteria_versions', {**service_values(), 'source_requirements_text': ' \t'}, 'domain'),
    ('criteria_versions', {**service_values(), 'request_hash_version': ' '}, 'domain'),
    ('criteria_versions', {**service_values(), 'confirmation_state': 'unconfirmed'}, 'domain'),
    ('criteria_versions', {'origin_kind': 'imported'}, 'domain'),
    ('criteria_versions', {'origin_kind': 'imported', 'import_package_id': '@uuid', 'source_snapshot_jsonb': None}, 'domain'),
    ('criteria_items', {'position': 0}, 'domain'),
    ('criteria_items', {'position': -1}, 'domain'),
    ('criteria_items', {'category': 'unsupported'}, 'domain'),
    ('criteria_items', {'text': ' \t'}, 'domain'),
    ('criteria_items', {'external_item_id': ' \n'}, 'domain'),
    ('criteria_items', {'criteria_version_id': None}, '23502'),
]
for name in ('criteria_sets', 'criteria_drafts'):
    INVALID.extend((name, {col: 'infinity'}, 'times') for col in ('created_at', 'updated_at'))
    INVALID.append((name, {'updated_at': '2000-01-01T00:00:00Z'}, 'times'))
for col in ('created_at', 'sealed_at'):
    INVALID.append(('criteria_versions', {col: 'infinity'}, 'times'))
INVALID.append(('criteria_versions', {**service_values(), 'confirmed_at': '-infinity'}, 'times'))
for col in ('source_draft_id', 'source_draft_revision', 'source_draft_hash', 'source_requirements_text',
            'confirmed_by', 'confirmed_at', 'confirmation_key', 'confirmation_request_hash', 'request_hash_version'):
    changes = service_values()
    changes[col] = None
    INVALID.append(('criteria_versions', changes, 'domain'))
for origin in ('imported', 'legacy_snapshot'):
    for col in ('confirmed_by', 'confirmed_at', 'confirmation_key', 'confirmation_request_hash', 'request_hash_version', 'confirmation_state'):
        value = {'confirmed_by': '@uuid', 'confirmed_at': NOW, 'confirmation_key': '@uuid',
                 'confirmation_request_hash': 'd'*64, 'request_hash_version': 'fixture-request-v1', 'confirmation_state': 'confirmed'}[col]
        INVALID.append(('criteria_versions', {'origin_kind': origin, 'import_package_id': '@uuid', col: value}, 'domain'))
for col in ('criteria_hash', 'source_draft_hash', 'confirmation_request_hash'):
    for bad in ('A'*64, 'a'*63, 'g'*64, ''):
        changes = {col: bad}
        if col == 'confirmation_request_hash':
            changes = {**service_values(), col: bad}
        INVALID.append(('criteria_versions', changes, 'hashes'))
for name, columns in {
    'criteria_drafts': ('items_jsonb', 'source_documents_jsonb'),
    'criteria_versions': ('source_documents_jsonb',),
}.items():
    for col in columns:
        INVALID.extend((name, {col: value}, 'json_shapes') for value in ({}, 'not array', 1, '@json_null'))
for name, columns in {'criteria_versions': ('source_snapshot_jsonb',),
                      'criteria_items': ('source_locator', 'applicability_jsonb', 'check_scope_jsonb', 'source_item_jsonb')}.items():
    for col in columns:
        INVALID.extend((name, {col: value}, 'json_shapes') for value in ([], 'not object', 1, '@json_null'))


@pytest.mark.parametrize('name,changes,expected', INVALID)
def test_criteria_checks_reject_invalid_rows(criteria_database, name, changes, expected):
    from sqlalchemy import JSON
    from sqlalchemy.exc import DBAPIError
    _, owner, _, user, project = criteria_database
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


@pytest.mark.parametrize('name', CRITERIA_NAMES)
def test_primary_keys_reject_duplicates(criteria_database, name):
    from sqlalchemy.exc import IntegrityError
    _, owner, _, user, project = criteria_database
    with owner_rollback(owner) as connection:
        body = record(name, project, user)
        insert(connection, name, body)
        with pytest.raises(IntegrityError) as caught:
            insert(connection, name, body)
        assert caught.value.orig.sqlstate == '23505'


@pytest.mark.parametrize('key', ('project_version', 'user_confirmation_key', 'item_position', 'item_external_id'))
def test_unique_keys_and_scopes(criteria_database, key):
    from sqlalchemy.exc import IntegrityError
    _, owner, _, user, project = criteria_database
    name = 'criteria_versions' if key in ('project_version', 'user_confirmation_key') else 'criteria_items'
    first, duplicate = record(name, project, user), record(name, project, user)
    if key == 'user_confirmation_key':
        first.update(service_values())
        duplicate.update(service_values(), project_id=uuid4(), confirmed_by=first['confirmed_by'], confirmation_key=first['confirmation_key'])
    if key == 'item_position':
        duplicate.update(criteria_version_id=first['criteria_version_id'])
    if key == 'item_external_id':
        first.update(external_item_id='criterion-1')
        duplicate.update(criteria_version_id=first['criteria_version_id'], external_item_id='criterion-1', position=2)
    with owner_rollback(owner) as connection:
        insert(connection, name, first)
        with pytest.raises(IntegrityError) as caught:
            insert(connection, name, duplicate)
        assert caught.value.orig.sqlstate == '23505'
        expected = {'project_version': 'uq_criteria_versions_project_version', 'user_confirmation_key': 'uq_criteria_versions_confirm_request',
                    'item_position': 'uq_criteria_items_position', 'item_external_id': 'uq_criteria_items_external_id'}[key]
        assert caught.value.orig.diag.constraint_name == expected


def test_editing_defaults_bigint_and_original_draft_array(criteria_database):
    from backend.app.persistence.schema import metadata
    _, owner, _, user, project = criteria_database
    with owner_rollback(owner) as connection:
        # Editing may be empty; selected and excluded items stay in order in the source array.
        draft = metadata.tables['public.criteria_drafts']
        original = [{'id': 'b', 'selected': False, 'text': '제외 원문'}, {'id': 'a', 'selected': True, 'text': '선택 원문'}]
        body = record('criteria_drafts', project, user)
        row = connection.execute(draft.insert().values(**body).returning(draft)).mappings().one()
        assert (row['revision'], row['state'], row['raw_requirements'], row['items_jsonb']) == (1, 'editing', '', [])
        for value in (2**53 + 1, 2**63 - 1):
            row = connection.execute(draft.insert().values(**{**body, 'draft_id': uuid4(), 'revision': value,
                'items_jsonb': original, 'source_documents_jsonb': []}).returning(draft)).mappings().one()
            assert row['revision'] == value and row['items_jsonb'] == original and row['source_documents_jsonb'] == []
        # Duplicate display names and an unknown historical creator are valid.
        for _ in range(2):
            row = connection.execute(metadata.tables['public.criteria_sets'].insert().values(**record('criteria_sets', project, user)).returning(metadata.tables['public.criteria_sets'])).mappings().one()
            assert row['created_by'] is None


@pytest.mark.parametrize('kind', ('service_confirmation', 'imported', 'legacy_snapshot'))
def test_origin_contracts_and_external_confirmation_remain_separate(criteria_database, kind):
    from backend.app.persistence.schema import metadata
    _, owner, _, user, project = criteria_database
    body = record('criteria_versions', project, user)
    if kind == 'service_confirmation':
        body.update(service_values(), source_draft_revision=2**53 + 1)
        body.pop('source_snapshot_jsonb'); body.pop('source_version_jsonb')
    elif kind == 'imported':
        body.update(origin_kind=kind, import_package_id=uuid4(), source_version_jsonb=None,
                    source_confirmation_state='confirmed', source_snapshot_jsonb={'confirmed': True, 'version': 'raw-v1'})
    with owner_rollback(owner) as connection:
        table = metadata.tables['public.criteria_versions']
        row = connection.execute(table.insert().values(**body).returning(table)).mappings().one()
        assert row['confirmation_state'] == ('confirmed' if kind == 'service_confirmation' else 'unconfirmed')
        assert row['sealed_at'] is None  # M007 will require seal at transaction completion.
        if kind != 'service_confirmation':
            assert all(row[col] is None for col in ('confirmed_by', 'confirmed_at', 'confirmation_key', 'confirmation_request_hash', 'request_hash_version'))


@pytest.mark.parametrize('source_value', (1, '1', 0, False, ['raw', 1], {'revision': 1}, '@json_null'))
def test_legacy_version_value_and_json_type_are_preserved(criteria_database, source_value):
    from sqlalchemy import JSON
    from backend.app.persistence.schema import metadata
    _, owner, _, user, project = criteria_database
    table = metadata.tables['public.criteria_versions']
    body = record('criteria_versions', project, user)
    body['source_version_jsonb'] = JSON.NULL if source_value == '@json_null' else source_value
    with owner_rollback(owner) as connection:
        insert(connection, 'criteria_versions', body)
        value, is_sql_null, kind = connection.exec_driver_sql('SELECT source_version_jsonb, source_version_jsonb IS NULL, jsonb_typeof(source_version_jsonb) FROM public.criteria_versions WHERE criteria_version_id=%s', (body['criteria_version_id'],)).one()
        assert not is_sql_null
        expected = None if source_value == '@json_null' else source_value
        assert value == expected and type(value) is type(expected)
        assert kind == ('null' if source_value == '@json_null' else 'boolean' if isinstance(source_value, bool) else
                        'number' if isinstance(source_value, int) else 'string' if isinstance(source_value, str) else
                        'array' if isinstance(source_value, list) else 'object')


def test_item_unknown_values_duplicate_rules_and_null_external_ids(criteria_database):
    from sqlalchemy import JSON
    from backend.app.persistence.schema import metadata
    _, owner, _, user, project = criteria_database
    table = metadata.tables['public.criteria_items']
    with owner_rollback(owner) as connection:
        base = record('criteria_items', project, user)
        rows = []
        for i, value in enumerate((None, JSON.NULL, False, 0, '', [], {}, '명시 기대값'), 1):
            body = {**base, 'criteria_item_id': uuid4(), 'position': i, 'rule_code': 'venue_contains', 'expected_value': value,
                    'category': 'unknown', 'source_item_jsonb': {'unrecognized_category': 'raw', **({'expected_value': None} if i == 2 else {})}}
            rows.append(connection.execute(table.insert().values(**body).returning(table)).mappings().one())
        assert all(r['is_required'] is None and r['external_item_id'] is None for r in rows)
        sql_null = connection.exec_driver_sql('SELECT position, expected_value IS NULL FROM public.criteria_items ORDER BY position').all()
        assert sql_null[:2] == [(1, True), (2, False)]
        # Same external item ID is valid in different versions; raw spacing is preserved.
        for _ in range(2):
            insert(connection, 'criteria_items', {**record('criteria_items', project, user), 'external_item_id': ' Raw:criterion/1 ', 'is_required': False})


def test_same_content_and_distinct_request_contexts_create_distinct_versions(criteria_database):
    from sqlalchemy import select
    from backend.app.persistence.schema import metadata
    _, owner, _, user, project = criteria_database
    table = metadata.tables['public.criteria_versions']
    with owner_rollback(owner) as connection:
        base = record('criteria_versions', project, user)
        insert(connection, 'criteria_versions', base)
        insert(connection, 'criteria_versions', {**base, 'criteria_version_id': uuid4(), 'version_no': 2})
        insert(connection, 'criteria_versions', {**base, 'criteria_version_id': uuid4(), 'project_id': uuid4()})
        service = {**base, **service_values(), 'criteria_version_id': uuid4(), 'version_no': 3}
        insert(connection, 'criteria_versions', service)
        insert(connection, 'criteria_versions', {**service, 'criteria_version_id': uuid4(), 'version_no': 4, 'confirmation_key': uuid4()})
        insert(connection, 'criteria_versions', {**service, 'criteria_version_id': uuid4(), 'version_no': 5, 'confirmed_by': uuid4()})
        assert connection.execute(select(table.c.criteria_hash)).scalars().all() == ['a'*64]*6


@pytest.mark.parametrize('key', ('project_version', 'user_confirmation_key'))
def test_concurrent_unique_reservations_have_one_winner_without_silent_merge(criteria_database, key):
    from sqlalchemy import delete, select
    from sqlalchemy.exc import IntegrityError
    from backend.app.persistence.schema import metadata
    _, owner, _, user, project = criteria_database
    table = metadata.tables['public.criteria_versions']
    marker_project = uuid4()
    first = {**record('criteria_versions', marker_project, user), **service_values()}
    second = {**first, 'criteria_version_id': uuid4()}
    if key == 'project_version':
        second.update(criteria_set_id=uuid4(), confirmation_key=uuid4())
    else:
        second.update(version_no=2)
    barrier = threading.Barrier(2)
    def write(body):
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql("SET LOCAL lock_timeout='5s'")
                connection.exec_driver_sql("SET LOCAL statement_timeout='10s'")
                barrier.wait(timeout=5)
                insert(connection, 'criteria_versions', body)
            return 'committed'
        except IntegrityError as error:
            return error.orig.sqlstate
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(write, (first, second)))
        assert sorted(results) == ['23505', 'committed']
        with owner.connect() as connection:
            assert len(connection.execute(select(table.c.criteria_version_id).where(table.c.project_id == marker_project)).all()) == 1
    finally:
        # Only generated rows inside the generated test database; no unrelated cleanup.
        with owner.begin() as connection:
            connection.execute(delete(table).where(table.c.project_id == marker_project))


def test_app_permissions_repeat_upgrade_and_downgrade_guard(criteria_database):
    from alembic import command
    from alembic.util import CommandError
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    _, owner, configuration, _, _ = criteria_database
    command.upgrade(configuration(), 'M004')
    with pytest.raises(CommandError, match='M004 destructive downgrade is disabled'):
        command.downgrade(configuration(), 'M003')
    db = criteria_database[0]
    with owner.connect() as connection:
        for name in (*CRITERIA_NAMES, 'alembic_version'):
            assert not connection.execute(text("SELECT has_table_privilege(:app,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),
                {'app': db.settings.database.username, 'table': 'public.' + name}).scalar_one()
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one() == 'M004'
    with db.engine.connect() as connection:
        with pytest.raises(DBAPIError) as caught:
            connection.exec_driver_sql('SELECT * FROM public.criteria_versions')
        assert caught.value.orig.sqlstate == '42501'
