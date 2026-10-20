"""The complete implemented revision chain is atomic from a fresh public schema."""
from pathlib import Path
import shutil

import pytest

from backend.tests.test_db_integration import database  # noqa: F401

pytestmark=pytest.mark.db_integration


def test_fresh_database_upgrade_to_current_head_is_atomic(database,tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError
    from sqlalchemy import inspect
    from backend.app.persistence.schema import metadata
    from backend.migrations.configuration import create_migration_engine,load_migration_settings
    env=database.environment
    settings=load_migration_settings({"DB_HOST":env["TEST_DB_HOST"],"DB_PORT":env["TEST_DB_PORT"],
        "POSTGRES_DB":database.name,"DB_OWNER_USER":env["TEST_DB_OWNER_USER"],
        "DB_OWNER_PASSWORD":env["TEST_DB_OWNER_PASSWORD"],"DB_APP_USER":env["TEST_DB_APP_USER"]})
    def configuration(directory=None):
        config=Config(str(Path("backend/migrations/alembic.ini").resolve()))
        config.attributes["migration_settings"]=settings
        if directory:config.set_main_option("script_location",str(directory))
        return config
    owner=create_migration_engine(settings)
    try:
        directory=tmp_path/"migrations"
        shutil.copytree("backend/migrations",directory,ignore=shutil.ignore_patterns("__pycache__"))
        scripts=ScriptDirectory.from_config(configuration(directory));head=scripts.get_current_head()
        revision=Path(scripts.get_revision(head).path)
        source=revision.read_text()
        assert '\n\ndef downgrade():' in source
        revision.write_text(source.replace('\n\ndef downgrade():','\n    op.get_bind().exec_driver_sql("SELECT 1 / 0")\n\ndef downgrade():'))
        with owner.connect() as connection:
            before=connection.exec_driver_sql("SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2").all()
        with pytest.raises(CommandError,match="SQLSTATE=22012"):
            command.upgrade(configuration(directory),"head")
        with owner.connect() as connection:
            assert inspect(connection).get_table_names(schema="public")==[]
            assert connection.exec_driver_sql("SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace").scalar_one()==0
            assert connection.exec_driver_sql("SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2").all()==before
        command.upgrade(configuration(),"head")
        with owner.connect() as connection:
            assert connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one()==head
            assert set(inspect(connection).get_table_names(schema="public"))=={t.name for t in metadata.tables.values()}|{"alembic_version"}
            for table in metadata.tables.values():
                assert connection.exec_driver_sql(f'SELECT count(*) FROM public."{table.name}"').scalar_one()==0
    finally:
        owner.dispose()
