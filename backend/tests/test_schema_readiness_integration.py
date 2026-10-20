"""Every partial revision is rejected; the complete empty M008 can be inspected by app."""
from pathlib import Path
import pytest
from backend.tests.test_db_integration import database  # noqa: F401
pytestmark=pytest.mark.db_integration


def test_readiness_rejects_empty_and_every_partial_revision(database):
    from alembic import command
    from alembic.config import Config
    from backend.migrations.configuration import create_migration_engine,load_migration_settings
    from backend.app.persistence.schema_inventory import check_schema,validate_schema
    from backend.app.persistence.database import PersistenceError
    from backend.app.persistence.schema import metadata
    env=database.environment
    settings=load_migration_settings({'DB_HOST':env['TEST_DB_HOST'],'DB_PORT':env['TEST_DB_PORT'],'POSTGRES_DB':database.name,
        'DB_OWNER_USER':env['TEST_DB_OWNER_USER'],'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'],'DB_APP_USER':env['TEST_DB_APP_USER']})
    config=Config(str(Path('backend/migrations/alembic.ini').resolve()));config.attributes['migration_settings']=settings
    owner=create_migration_engine(settings)
    try:
        with pytest.raises(PersistenceError) as error:check_schema(database.engine,database.settings)
        assert error.value.code=='DB_SCHEMA_UNREADABLE'
        for number in range(1,8):
            command.upgrade(config,f'M{number:03d}')
            with owner.connect() as connection:
                with pytest.raises(PersistenceError) as error:validate_schema(connection,app_user=database.settings.database.username)
                assert error.value.code=='DB_SCHEMA_INCOMPLETE'
            with pytest.raises(PersistenceError) as error:check_schema(database.engine,database.settings)
            assert error.value.code=='DB_SCHEMA_UNREADABLE'  # Alembic SELECT is intentionally closed before M008.
        command.upgrade(config,'head')
        assert check_schema(database.engine,database.settings)['status']=='schema_ready'
        with database.engine.connect() as connection:
            for table in metadata.tables.values():assert connection.exec_driver_sql(f'SELECT count(*) FROM public."{table.name}"').scalar_one()==0
    finally:owner.dispose()
