"""M008 atomically narrows existing ACLs without touching rows or guard definitions."""
from pathlib import Path
import shutil
import pytest
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_guard_migration_integration import guards_database  # noqa: F401
from backend.tests.test_identity_migration_integration import owner_rollback

pytestmark=pytest.mark.db_integration


def test_m008_final_failure_and_success_preserve_existing_business_rows(guards_database,tmp_path):
    from alembic import command
    from alembic.util import CommandError
    from backend.app.persistence.schema import metadata
    from backend.app.persistence.schema_inventory import capture_structure,capture_privileges,check_schema
    db,owner,configuration,*_=guards_database;quote=owner.dialect.identifier_preparer.quote
    app=quote(db.settings.database.username)
    with owner.begin() as connection:
        # Existing broad COLUMN rights need separate revocation from TABLE rights.
        connection.exec_driver_sql(f'GRANT ALL ON public.users TO {app},PUBLIC')
        connection.exec_driver_sql(f'GRANT UPDATE(created_at) ON public.users TO {app},PUBLIC')
        connection.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION public.cc_fail(text) TO {app},PUBLIC')
        for scope in ('',' IN SCHEMA public'):
            for kind in ('TABLES','SEQUENCES','FUNCTIONS'):
                connection.exec_driver_sql(f'ALTER DEFAULT PRIVILEGES{scope} GRANT ALL ON {kind} TO {app},PUBLIC')
    def snapshot():
        with owner.connect() as connection:
            return {'structure':capture_structure(connection),'privileges':capture_privileges(connection,db.settings.database.username),
                'objects':connection.exec_driver_sql("SELECT oid::text,relname,relacl::text FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY 2").all(),
                'column_acl':connection.exec_driver_sql("SELECT attrelid::text,attnum,attacl::text FROM pg_attribute WHERE attrelid IN (SELECT oid FROM pg_class WHERE relnamespace='public'::regnamespace) AND attnum>0 ORDER BY 1,2").all(),
                'functions':connection.exec_driver_sql("SELECT oid::text,pg_get_functiondef(oid),proacl::text FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY 1").all(),
                'schema_acl':connection.exec_driver_sql("SELECT nspacl::text FROM pg_namespace WHERE nspname='public'").all(),
                'db_acl':connection.exec_driver_sql("SELECT datacl::text FROM pg_database WHERE datname=current_database()").all(),
                'defaults':connection.exec_driver_sql('SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2').all(),
                'rows':[connection.exec_driver_sql(f'SELECT to_jsonb(t)::text FROM public."{t.name}" t ORDER BY to_jsonb(t)::text').all() for t in metadata.tables.values()],
                'revision':connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one()}
    before=snapshot();assert before['revision']=='M007'
    directory=tmp_path/'migrations';shutil.copytree('backend/migrations',directory,ignore=shutil.ignore_patterns('__pycache__'))
    path=directory/'versions/M008_grants.py'
    path.write_text(path.read_text().replace('\n\ndef downgrade():','\n    op.execute("SELECT 1 / 0")\n\ndef downgrade():'))
    with pytest.raises(CommandError,match='SQLSTATE=22012'):command.upgrade(configuration(directory),'M008')
    assert snapshot()==before
    command.upgrade(configuration(),'M008');after=snapshot()
    assert after['revision']=='M008' and after['rows']==before['rows']
    assert after['structure']==before['structure']
    assert [r[:2] for r in after['objects']]==[r[:2] for r in before['objects']]
    assert [r[:2] for r in after['functions']]==[r[:2] for r in before['functions']]
    assert check_schema(db.engine,db.settings)['status']=='schema_ready'
    command.upgrade(configuration(),'M008');assert snapshot()==after
    with pytest.raises(CommandError,match='M008 destructive downgrade is disabled'):command.downgrade(configuration(),'M007')
    assert snapshot()==after
