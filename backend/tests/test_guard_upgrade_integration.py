"""M007 validates existing rows and never adopts conflicting objects or partial DDL."""
from pathlib import Path
import shutil

import pytest
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.relation_fixtures import create_graph

pytestmark=pytest.mark.db_integration


def test_existing_rows_collision_and_final_failure_are_atomic(database,tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.util import CommandError
    from backend.app.persistence.schema import metadata
    from backend.migrations.configuration import create_migration_engine,load_migration_settings
    env=database.environment
    settings=load_migration_settings({'DB_HOST':env['TEST_DB_HOST'],'DB_PORT':env['TEST_DB_PORT'],'POSTGRES_DB':database.name,
        'DB_OWNER_USER':env['TEST_DB_OWNER_USER'],'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'],'DB_APP_USER':env['TEST_DB_APP_USER']})
    def config(directory=None):
        result=Config(str(Path('backend/migrations/alembic.ini').resolve()));result.attributes['migration_settings']=settings
        if directory:result.set_main_option('script_location',str(directory))
        return result
    owner=create_migration_engine(settings)
    def snapshot():
        with owner.connect() as connection:
            return (
                connection.exec_driver_sql("SELECT relname,oid::text FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY 1").all(),
                connection.exec_driver_sql("SELECT oid::text,pg_get_functiondef(oid) FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY 1").all(),
                # Trigger names repeat across tables; include the table for stable
                # comparison even when PostgreSQL changes equal-name row order.
                connection.exec_driver_sql("SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='public'::regnamespace AND NOT t.tgisinternal ORDER BY 1,2").all(),
                connection.exec_driver_sql('SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2').all(),
                [connection.exec_driver_sql(f'SELECT to_jsonb(t)::text FROM public."{t.name}" t ORDER BY to_jsonb(t)::text').all() for t in metadata.tables.values()],
                connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one())
    try:
        command.upgrade(config(),'M006')
        with owner.begin() as connection:create_graph(connection)
        before=snapshot()
        # This historical M006 fixture has a same-shape but wrong input manifest hash.
        with pytest.raises(CommandError,match='SQLSTATE=23514'):command.upgrade(config(),'M007')
        assert snapshot()==before
        # Explicit test-data preparation is outside the migration, never automatic repair.
        with owner.begin() as connection:
            connection.exec_driver_sql('UPDATE public.item_results i SET input_manifest_hash=r.input_manifest_hash FROM public.review_runs r WHERE r.run_id=i.run_id')
        valid=snapshot()
        with owner.begin() as connection:
            connection.exec_driver_sql('CREATE FUNCTION public.cc_fail(text) RETURNS void LANGUAGE plpgsql AS $$ BEGIN RETURN; END $$')
        collision=snapshot()
        with pytest.raises(CommandError,match='SQLSTATE=42723'):command.upgrade(config(),'M007')
        assert snapshot()==collision
        with owner.begin() as connection:connection.exec_driver_sql('DROP FUNCTION public.cc_fail(text)')
        assert snapshot()==valid
        directory=tmp_path/'migrations';shutil.copytree('backend/migrations',directory,ignore=shutil.ignore_patterns('__pycache__'))
        revision=directory/'versions/M007_guards.py'
        revision.write_text(revision.read_text().replace('\n\ndef downgrade():','\n    op.execute("SELECT 1 / 0")\n\ndef downgrade():'))
        with pytest.raises(CommandError,match='SQLSTATE=22012'):command.upgrade(config(directory),'M007')
        assert snapshot()==valid
        command.upgrade(config(),'M007');after=snapshot()
        assert set(valid[0]).issubset(set(after[0])) and len(after[0])-len(valid[0])==27
        assert len(after[1])==19 and len(after[2])==35
        assert after[3:5]==valid[3:5] and after[5]=='M007'
        command.upgrade(config(),'M007');assert snapshot()==after
        with pytest.raises(CommandError,match='M007 destructive downgrade is disabled'):command.downgrade(config(),'M006')
        assert snapshot()==after
    finally:owner.dispose()
