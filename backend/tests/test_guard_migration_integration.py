"""M007 catalog, upgrade atomicity and real PostgreSQL guard behavior."""
from pathlib import Path
import json
import shutil
from uuid import uuid4

import pytest

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_identity_migration_integration import owner_rollback
from backend.tests.guard_fixtures import create_guard_graph, insert_row, row_where

pytestmark=pytest.mark.db_integration


@pytest.fixture(scope='module')
def guards_database(database):
    from alembic import command
    from alembic.config import Config
    from backend.migrations.configuration import create_migration_engine,load_migration_settings
    env=database.environment
    settings=load_migration_settings({'DB_HOST':env['TEST_DB_HOST'],'DB_PORT':env['TEST_DB_PORT'], 'POSTGRES_DB':database.name,
        'DB_OWNER_USER':env['TEST_DB_OWNER_USER'],'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'],'DB_APP_USER':env['TEST_DB_APP_USER']})
    def configuration(directory=None):
        config=Config(str(Path('backend/migrations/alembic.ini').resolve()))
        config.attributes['migration_settings']=settings
        if directory:config.set_main_option('script_location',str(directory))
        return config
    owner=create_migration_engine(settings)
    try:
        command.upgrade(configuration(),'M007')
        with owner.begin() as connection:
            main=create_guard_graph(connection)
            other=create_guard_graph(connection,user=main['users'],project=main['projects'],version_no=2)
        yield database,owner,configuration,main,other
    finally:owner.dispose()


def test_m007_graph_commits_and_catalog(guards_database,request):
    from backend.app.persistence.schema import metadata
    _,owner,_,main,_=guards_database
    with owner.connect() as connection:
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one()=='M007'
        triggers=connection.exec_driver_sql("SELECT t.tgname,c.relname,t.tgdeferrable,t.tginitdeferred,t.tgtype,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='public'::regnamespace AND NOT t.tgisinternal ORDER BY 1,2").all()
        from backend.tests.test_guard_schema import guard_contract
        assert sorted((t[0],t[1]) for t in triggers)==sorted(guard_contract())
        request.node.user_properties.append(('guard_catalog',json.dumps([list(t) for t in triggers])))
        assert len(triggers)==35
        assert sum(t[2] for t in triggers)==18
        assert all(t[2]==t[3] and t[5]=='O' for t in triggers)
        assert len({t[0] for t in triggers})==30
        assert connection.exec_driver_sql("SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace AND starts_with(proname,'cc_')").scalar_one()==19
        assert connection.exec_driver_sql("SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relnamespace='public'::regnamespace AND i.indrelid<>'public.alembic_version'::regclass").scalar_one()==94
        assert sum(len(t.indexes) for t in metadata.tables.values())==27
        assert connection.exec_driver_sql('SELECT count(*) FROM public.run_item_selections').scalar_one()==2
        functions=connection.exec_driver_sql("SELECT proname,prosecdef,provolatile,proconfig,proacl::text FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY proname").all()
        assert len(functions)==19 and all(not f[1] and f[2]=='v' and f[3]==['search_path=pg_catalog, public'] for f in functions)
        request.node.user_properties.append(('guard_functions',json.dumps([list(f) for f in functions])))
        from sqlalchemy import text
        for table in metadata.tables.values():
            assert not connection.execute(text("SELECT has_table_privilege(:app,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),{'app':guards_database[0].settings.database.username,'table':'public.'+table.name}).scalar_one()
        assert not connection.execute(text("SELECT bool_or(has_function_privilege(:app,p.oid,'EXECUTE')) FROM pg_proc p WHERE pronamespace='public'::regnamespace"),{'app':guards_database[0].settings.database.username}).scalar_one()
    from sqlalchemy.exc import DBAPIError
    with guards_database[0].engine.connect() as connection:
        with pytest.raises(DBAPIError) as error:connection.exec_driver_sql('SELECT * FROM public.review_runs')
        assert error.value.orig.sqlstate=='42501'
