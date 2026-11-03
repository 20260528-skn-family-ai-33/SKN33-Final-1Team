"""Direct M008 -> AF003 upgrade in a brand-new disposable database."""
import pytest
from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database
from backend.migrations.configuration import load_migration_settings
from backend.agent_framework.postgres_schema import migrate,check_app_permissions,check_criteria_extension
from backend.app.persistence.schema_inventory import check_schema
from backend.app.persistence.transaction import unit_of_work

pytestmark=pytest.mark.db_integration


def test_direct_upgrade_keeps_existing_public_schema_and_restricted_roles(auth_database):
    db,*_=auth_database;env=db.environment
    settings=load_migration_settings({'DB_HOST':env['TEST_DB_HOST'],'DB_PORT':env['TEST_DB_PORT'],
        'POSTGRES_DB':db.name,'DB_OWNER_USER':env['TEST_DB_OWNER_USER'],
        'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'],'DB_APP_USER':env['TEST_DB_APP_USER']})
    before=check_schema(db.engine,db.settings)
    assert migrate(settings,target='AF003')['revision']=='AF003'
    assert migrate(settings,target='AF001')['revision']=='AF003'
    assert check_schema(db.engine,db.settings)==before
    with unit_of_work(db.engine) as c:
        check_criteria_extension(c);check_app_permissions(c)
