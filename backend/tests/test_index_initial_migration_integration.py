"""A fresh test DB rolls back the entire staged chain if the index draft fails."""
from pathlib import Path

import pytest

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.index_fixtures import stage_index_draft

pytestmark=pytest.mark.db_integration


def test_fresh_staged_index_chain_is_atomic_and_business_tables_stay_empty(database,tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.util import CommandError
    from sqlalchemy import inspect
    from backend.app.persistence.schema import metadata
    from backend.migrations.configuration import create_migration_engine,load_migration_settings

    env=database.environment
    settings=load_migration_settings({
        'DB_HOST':env['TEST_DB_HOST'],'DB_PORT':env['TEST_DB_PORT'],'POSTGRES_DB':database.name,
        'DB_OWNER_USER':env['TEST_DB_OWNER_USER'],'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'],
        'DB_APP_USER':env['TEST_DB_APP_USER'],
    })
    directory=stage_index_draft(tmp_path)
    def configuration():
        config=Config(str(Path('backend/migrations/alembic.ini').resolve()))
        config.set_main_option('script_location',str(directory))
        config.attributes.update(migration_settings=settings,allow_c07a_index_draft=True)
        return config

    revision=directory/'versions/M007_indexes_draft.py'
    source=revision.read_text()
    revision.write_text(source.replace('\n\ndef downgrade():',
        '\n    op.get_bind().exec_driver_sql("SELECT 1 / 0")\n\ndef downgrade():'))
    owner=create_migration_engine(settings)
    try:
        with owner.connect() as connection:
            before=connection.exec_driver_sql('SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2').all()
        with pytest.raises(CommandError,match='SQLSTATE=22012'):
            command.upgrade(configuration(),'M007')
        with owner.connect() as connection:
            assert inspect(connection).get_table_names(schema='public')==[]
            assert connection.exec_driver_sql('SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2').all()==before
        revision.write_text(source)
        command.upgrade(configuration(),'M007')
        with owner.connect() as connection:
            assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one()=='M007'
            assert set(inspect(connection).get_table_names(schema='public'))=={t.name for t in metadata.tables.values()}|{'alembic_version'}
            assert connection.exec_driver_sql("SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relnamespace='public'::regnamespace AND i.indrelid<>'public.alembic_version'::regclass").scalar_one()==94
            for table in metadata.tables.values():
                assert connection.exec_driver_sql(f'SELECT count(*) FROM public."{table.name}"').scalar_one()==0
    finally:
        owner.dispose()
