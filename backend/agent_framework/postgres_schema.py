"""Explicit owner migration and read-only extension preflight; never on import."""
from hashlib import sha256
import json
from pathlib import Path

from sqlalchemy import text

from backend.app.persistence.database import PersistenceError
from backend.app.persistence.schema_inventory import _STRUCTURE_QUERIES,validate_schema

DDL = Path(__file__).with_name('AF001.sql')
DOCUMENT_DDL = Path(__file__).with_name('AF002.sql')
CRITERIA_DDL = Path(__file__).with_name('AF003.sql')
TEXT_DDL = Path(__file__).with_name('AF004.sql')
TABLES = ('schema_version', 'runs', 'jobs', 'steps', 'approvals', 'events')
DOCUMENT_TABLES = ('document_versions',)
CRITERIA_TABLES = ('criteria_versions','criteria_approval_bindings')
MIGRATIONS={'AF001':(DDL,TABLES),'AF002':(DOCUMENT_DDL,DOCUMENT_TABLES),'AF003':(CRITERIA_DDL,CRITERIA_TABLES),'AF004':(TEXT_DDL,())}
UPDATES = {'runs': ('snapshot_json','updated_at'),
           'jobs': ('state','attempt_no','worker_token','lease_expires_at','heartbeat_at','finished_at','error_json'),
           'steps': ('state','record_json','output_json','output_sha256')}


def _structure(connection):
    connection.exec_driver_sql('SET LOCAL search_path = pg_catalog, public')
    return {name: [list(row) for row in connection.exec_driver_sql(
        sql.replace("'public'", "'agent_framework'")).all()]
        for name, sql in _STRUCTURE_QUERIES.items()}


def _ddl_digest(revision):
    items=list(MIGRATIONS)
    return sha256(b'\0'.join(MIGRATIONS[name][0].read_bytes() for name in items[:items.index(revision)+1])).hexdigest()


def _tables(revision):
    names=list(MIGRATIONS)
    return tuple(table for name in names[:names.index(revision)+1] for table in MIGRATIONS[name][1])


def check_extension(connection):
    try:
        row = connection.exec_driver_sql('SELECT revision, ddl_sha256, structure_json FROM agent_framework.schema_version').one()
        expected = {revision:_ddl_digest(revision) for revision in MIGRATIONS}
        if row[0] not in expected or row[1] != expected[row[0]] or row[2] != _structure(connection):
            raise ValueError()
    except Exception:
        raise PersistenceError('FRAMEWORK_SCHEMA_NOT_READY',
            'Apply and verify the explicit framework extension before starting this profile.') from None
    return {'revision':row[0],'schema':'agent_framework','public_schema':'M008 unchanged'}


def check_document_extension(connection):
    result=check_extension(connection)
    if result['revision'] not in ('AF002','AF003','AF004'):
        raise PersistenceError('FRAMEWORK_DOCUMENT_SCHEMA_NOT_READY',
                               'Apply the explicit AF002 extension before enabling PDF registration.')
    return result


def check_criteria_extension(connection):
    result=check_extension(connection)
    if result['revision'] not in ('AF003','AF004'):
        raise PersistenceError('FRAMEWORK_CRITERIA_SCHEMA_NOT_READY','Apply AF003 explicitly before criteria revision/approval commands.')
    return result


def check_app_permissions(connection):
    """The extension must not turn the app into a schema owner or log editor."""
    forbidden=connection.exec_driver_sql("""SELECT
      has_schema_privilege(current_user,'agent_framework','CREATE') OR
      has_table_privilege(current_user,'agent_framework.schema_version','INSERT') OR
      has_table_privilege(current_user,'agent_framework.approvals','UPDATE') OR
      has_table_privilege(current_user,'agent_framework.events','UPDATE') OR
      has_table_privilege(current_user,'agent_framework.steps','DELETE') OR
      has_table_privilege(current_user,'agent_framework.runs','DELETE')""").scalar_one()
    revision=connection.exec_driver_sql('SELECT revision FROM agent_framework.schema_version').scalar_one()
    tables=_tables(revision)
    for table in tables:
        if table in DOCUMENT_TABLES+CRITERIA_TABLES:
            forbidden=forbidden or connection.execute(text("SELECT has_table_privilege(current_user,:table,'UPDATE') OR has_table_privilege(current_user,:table,'DELETE')"),{'table':'agent_framework.'+table}).scalar_one()
    required=all(connection.execute(text("SELECT has_table_privilege(current_user,:table,'SELECT')"),
        {'table':'agent_framework.'+name}).scalar_one() for name in tables)
    columns=connection.exec_driver_sql("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='agent_framework'").all()
    columns_match=all(connection.execute(text("SELECT has_column_privilege(current_user,:table,:column,'UPDATE')"),
        {'table':'agent_framework.'+name,'column':column}).scalar_one()==(column in UPDATES.get(name,()))
        for name,column in columns)
    tables_match=all(connection.execute(text("SELECT has_table_privilege(current_user,:table,'INSERT')"),
        {'table':'agent_framework.'+name}).scalar_one()==(name!='schema_version') for name in tables)
    if forbidden or not required or not columns_match or not tables_match:
        raise PersistenceError('FRAMEWORK_PRIVILEGES_INVALID','Restricted extension permissions are required.')


def migrate(settings, *, target='AF001'):
    if target not in MIGRATIONS:raise ValueError('Unsupported framework revision')
    from backend.migrations.configuration import create_migration_engine, validate_migration_connection
    engine = create_migration_engine(settings)
    try:
        with engine.begin() as connection:
            validate_migration_connection(connection, settings)
            connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
            connection.exec_driver_sql("SET LOCAL statement_timeout='60s'")
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended(current_database() || ':cc-schema-v1',0))")
            if connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalars().all() != ['M008']:
                raise ValueError('M008 must be applied first')
            validate_schema(connection,app_user=settings.app_user)
            names=list(MIGRATIONS);current=-1
            if connection.exec_driver_sql("SELECT to_regnamespace('agent_framework')").scalar_one() is not None:
                result=check_extension(connection)
                current=names.index(result['revision'])
                if current>=names.index(target):return result
            app = connection.dialect.identifier_preparer.quote(settings.app_user)
            for revision in names[current+1:names.index(target)+1]:
                ddl,tables=MIGRATIONS[revision]
                connection.exec_driver_sql(ddl.read_text(encoding='utf-8'))
                connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA agent_framework TO {app}')
                for table in tables:
                    connection.exec_driver_sql(f'REVOKE ALL ON agent_framework.{table} FROM PUBLIC')
                    connection.exec_driver_sql(f'GRANT SELECT ON agent_framework.{table} TO {app}')
                    if table!='schema_version':connection.exec_driver_sql(f'GRANT INSERT ON agent_framework.{table} TO {app}')
                    if table in UPDATES:
                        connection.exec_driver_sql(f'GRANT UPDATE ({",".join(UPDATES[table])}) ON agent_framework.{table} TO {app}')
            structure = _structure(connection)
            digest=_ddl_digest(target)
            connection.execute(text('INSERT INTO agent_framework.schema_version(revision,ddl_sha256,structure_json) '
                'VALUES(:revision,:hash,CAST(:structure AS jsonb)) ON CONFLICT(singleton) DO UPDATE '
                'SET revision=excluded.revision,ddl_sha256=excluded.ddl_sha256,structure_json=excluded.structure_json'),
                {'revision':target,'hash':digest, 'structure':json.dumps(structure)})
            return check_extension(connection)
    finally:
        engine.dispose()
