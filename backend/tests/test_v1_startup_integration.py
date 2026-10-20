"""The actual app lifespan rejects empty/partial/owner DBs and never seeds/repairs."""
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.auth_fixtures import auth_environment
from backend.app.main import create_app
from backend.app.v1_config import load_v1_settings
from backend.app.persistence.database import PersistenceError

pytestmark=pytest.mark.db_integration


def test_actual_lifespan_fails_closed_without_seed_and_releases_connections(database):
    from alembic import command
    from alembic.config import Config
    from backend.migrations.configuration import create_migration_engine,load_migration_settings
    from backend.app.persistence.schema import metadata
    env=database.environment
    migration=load_migration_settings({'DB_HOST':env['TEST_DB_HOST'],'DB_PORT':env['TEST_DB_PORT'],'POSTGRES_DB':database.name,
        'DB_OWNER_USER':env['TEST_DB_OWNER_USER'],'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'],'DB_APP_USER':env['TEST_DB_APP_USER']})
    config=Config(str(Path('backend/migrations/alembic.ini').resolve()));config.attributes['migration_settings']=migration
    owner=create_migration_engine(migration);settings=load_v1_settings(auth_environment(database))
    try:
        for revision in (None,'M007'):
            if revision:command.upgrade(config,revision)
            app=create_app(settings=settings,engine=database.engine)
            with pytest.raises(PersistenceError):
                with TestClient(app):pass
            assert not app.state.ready and database.engine.pool.checkedout()==0
        command.upgrade(config,'head')
        with pytest.raises(PersistenceError) as error:
            with TestClient(create_app(settings=settings,engine=owner)):pass
        assert error.value.code=='DB_ROLE_REJECTED'
        with TestClient(create_app(settings=settings,engine=database.engine),base_url='http://localhost:8000',client=('127.0.0.1',1234)) as browser:
            assert browser.get('/api/v1/health').status_code==200
        with owner.connect() as connection:
            for table in metadata.tables.values():assert connection.exec_driver_sql(f'SELECT count(*) FROM public."{table.name}"').scalar_one()==0
        assert database.engine.pool.checkedout()==0
    finally:owner.dispose()
