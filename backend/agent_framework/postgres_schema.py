"""Explicit owner migration and read-only extension preflight; never on import."""
from hashlib import sha256
import json
from pathlib import Path

from sqlalchemy import text

from backend.app.persistence.database import PersistenceError
from backend.app.persistence.schema_inventory import _STRUCTURE_QUERIES,validate_schema

DDL = Path(__file__).with_name('AF001.sql')
TABLES = ('schema_version', 'runs', 'jobs', 'steps', 'approvals', 'events')
UPDATES = {'runs': ('snapshot_json','updated_at'),
           'jobs': ('state','attempt_no','worker_token','lease_expires_at','heartbeat_at','finished_at','error_json'),
           'steps': ('state','record_json','output_json','output_sha256')}


def _structure(connection):
    connection.exec_driver_sql('SET LOCAL search_path = pg_catalog, public')
    return {name: [list(row) for row in connection.exec_driver_sql(
        sql.replace("'public'", "'agent_framework'")).all()]
        for name, sql in _STRUCTURE_QUERIES.items()}


def check_extension(connection):
    try:
        row = connection.exec_driver_sql('SELECT revision, ddl_sha256, structure_json FROM agent_framework.schema_version').one()
        if row[0] != 'AF001' or row[1] != sha256(DDL.read_bytes()).hexdigest() or row[2] != _structure(connection):
            raise ValueError()
    except Exception:
        raise PersistenceError('FRAMEWORK_SCHEMA_NOT_READY',
            'Apply and verify the explicit AF001 extension before starting this profile.') from None
    return {'revision':'AF001','schema':'agent_framework','public_schema':'M008 unchanged'}


def check_app_permissions(connection):
    """The extension must not turn the app into a schema owner or log editor."""
    forbidden=connection.exec_driver_sql("""SELECT
      has_schema_privilege(current_user,'agent_framework','CREATE') OR
      has_table_privilege(current_user,'agent_framework.schema_version','INSERT') OR
      has_table_privilege(current_user,'agent_framework.approvals','UPDATE') OR
      has_table_privilege(current_user,'agent_framework.events','UPDATE') OR
      has_table_privilege(current_user,'agent_framework.steps','DELETE') OR
      has_table_privilege(current_user,'agent_framework.runs','DELETE')""").scalar_one()
    required=all(connection.execute(text("SELECT has_table_privilege(current_user,:table,'SELECT')"),
        {'table':'agent_framework.'+name}).scalar_one() for name in TABLES)
    columns=connection.exec_driver_sql("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='agent_framework'").all()
    columns_match=all(connection.execute(text("SELECT has_column_privilege(current_user,:table,:column,'UPDATE')"),
        {'table':'agent_framework.'+name,'column':column}).scalar_one()==(column in UPDATES.get(name,()))
        for name,column in columns)
    tables_match=all(connection.execute(text("SELECT has_table_privilege(current_user,:table,'INSERT')"),
        {'table':'agent_framework.'+name}).scalar_one()==(name!='schema_version') for name in TABLES)
    if forbidden or not required or not columns_match or not tables_match:
        raise PersistenceError('FRAMEWORK_PRIVILEGES_INVALID','Restricted extension permissions are required.')


def migrate(settings):
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
            if connection.exec_driver_sql("SELECT to_regnamespace('agent_framework')").scalar_one() is not None:
                return check_extension(connection)
            connection.exec_driver_sql(DDL.read_text(encoding='utf-8'))
            app = connection.dialect.identifier_preparer.quote(settings.app_user)
            connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA agent_framework TO {app}')
            for table in TABLES:
                connection.exec_driver_sql(f'REVOKE ALL ON agent_framework.{table} FROM PUBLIC')
                connection.exec_driver_sql(f'GRANT SELECT ON agent_framework.{table} TO {app}')
                if table != 'schema_version':
                    connection.exec_driver_sql(f'GRANT INSERT ON agent_framework.{table} TO {app}')
            for table, columns in UPDATES.items():
                connection.exec_driver_sql(f'GRANT UPDATE ({",".join(columns)}) ON agent_framework.{table} TO {app}')
            structure = _structure(connection)
            connection.execute(text('INSERT INTO agent_framework.schema_version(revision,ddl_sha256,structure_json) '
                'VALUES(:revision,:hash,CAST(:structure AS jsonb))'),
                {'revision':'AF001','hash':sha256(DDL.read_bytes()).hexdigest(), 'structure':json.dumps(structure)})
            return check_extension(connection)
    finally:
        engine.dispose()
