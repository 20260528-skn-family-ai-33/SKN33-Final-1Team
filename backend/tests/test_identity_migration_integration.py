"""M001 runs only against the existing isolated, randomly named test DB fixture."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from backend.tests.test_db_integration import database  # noqa: F401 -- reuse explicit test-only fixture

pytestmark = pytest.mark.db_integration


@pytest.fixture(scope="module")
def migrated(database, tmp_path_factory):
    from alembic import command
    from alembic.config import Config
    from alembic.util import CommandError
    from sqlalchemy import text
    from backend.migrations.configuration import create_migration_engine, load_migration_settings
    env = database.environment
    settings = load_migration_settings({"DB_HOST":env["TEST_DB_HOST"],"DB_PORT":env["TEST_DB_PORT"],
        "POSTGRES_DB":database.name,"DB_OWNER_USER":env["TEST_DB_OWNER_USER"],
        "DB_OWNER_PASSWORD":env["TEST_DB_OWNER_PASSWORD"],"DB_APP_USER":env["TEST_DB_APP_USER"]})
    owner = create_migration_engine(settings)
    def configuration(directory=None):
        config = Config(str(Path("backend/migrations/alembic.ini").resolve()))
        config.attributes["migration_settings"] = settings
        if directory:config.set_main_option("script_location", str(directory))
        return config
    try:
        # A pre-existing public object must survive a refused initial migration.
        with owner.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE public.c02_existing_marker (id integer)")
        with pytest.raises(CommandError, match="requires an empty public schema"):
            command.upgrade(configuration(), "M001")
        with owner.begin() as connection:
            assert connection.exec_driver_sql("SELECT to_regclass('public.c02_existing_marker') IS NOT NULL").scalar_one()
            assert connection.exec_driver_sql("SELECT to_regclass('public.alembic_version') IS NULL").scalar_one()
            connection.exec_driver_sql("DROP TABLE public.c02_existing_marker")
        # Reproduce global AND schema-specific broad grants in the test DB, including the tool table.
        quote = owner.dialect.identifier_preparer.quote
        with owner.begin() as connection:
            for scope in ("", " IN SCHEMA public"):
                for objects in ("TABLES", "SEQUENCES"):
                    connection.exec_driver_sql(f"ALTER DEFAULT PRIVILEGES FOR ROLE {quote(settings.owner_user)}{scope} GRANT ALL ON {objects} TO {quote(settings.app_user)}, PUBLIC")
        with owner.connect() as connection:
            before = connection.exec_driver_sql("SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl WHERE defaclrole = current_user::regrole ORDER BY 1,2").all()
        # A deliberate failure after all M001 operations must roll back DDL, version and default ACLs.
        directory = tmp_path_factory.mktemp("c02_failed_revision") / "migrations"
        shutil.copytree("backend/migrations", directory, ignore=shutil.ignore_patterns("__pycache__"))
        revision = directory / "versions/M001_identity.py"
        source = revision.read_text();source=source.replace('\n\ndef downgrade():', '\n    op.execute(text("SELECT 1 / 0"))\n\ndef downgrade():')
        revision.write_text(source)
        with pytest.raises(CommandError, match="SQLSTATE=22012"):
            command.upgrade(configuration(directory), "M001")
        with owner.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM pg_tables WHERE schemaname='public'").scalar_one() == 0
            assert connection.exec_driver_sql("SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl WHERE defaclrole = current_user::regrole ORDER BY 1,2").all() == before
        command.upgrade(configuration(), "M001")
        yield database, owner, configuration
    finally:
        owner.dispose()


def test_inventory_metadata_types_defaults_constraints_comments_and_no_seed(migrated):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import inspect
    from backend.tests.schema_helpers import metadata_before_relations
    metadata = metadata_before_relations(['users', 'user_sessions', 'projects', 'project_members'])
    database, owner, _ = migrated
    with owner.connect() as connection:
        inspector = inspect(connection)
        assert set(inspector.get_table_names(schema="public")) == {"alembic_version","users","user_sessions","projects","project_members"}
        assert connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one() == "M001"
        assert sum(len(inspector.get_columns(t.name,schema="public")) for t in metadata.tables.values()) == 30
        differences = compare_metadata(MigrationContext.configure(connection, opts={"include_schemas":True,
            "include_object":lambda obj,name,kind,reflected,compare_to: getattr(obj, "schema", "public") in ("public",None)
                and (kind != "table" or name not in {"alembic_version","records"}),
            "compare_server_default":True}),metadata)
        assert differences == [], differences
        for table in metadata.tables.values():
            assert connection.exec_driver_sql(f'SELECT count(*) FROM public."{table.name}"').scalar_one() == 0
            assert inspector.get_pk_constraint(table.name,schema="public")["name"] == table.primary_key.name
            actual = {c["name"] for c in inspector.get_check_constraints(table.name,schema="public")}
            expected = {c.name for c in table.constraints if c.__class__.__name__=="CheckConstraint"}
            assert actual == expected
            assert not inspector.get_foreign_keys(table.name,schema="public")
            assert not [x for x in inspector.get_indexes(table.name,schema="public") if not x.get("duplicates_constraint")]
            assert inspector.get_table_comment(table.name,schema="public")["text"] == table.comment
            assert all(x["comment"] for x in inspector.get_columns(table.name,schema="public"))


def test_app_has_no_table_or_version_privileges_and_new_defaults_stay_closed(migrated):
    from sqlalchemy import text
    database, owner, _ = migrated
    app = database.settings.database.username
    with owner.connect() as connection:
        for name in ("users","user_sessions","projects","project_members","alembic_version"):
            for privilege in ("SELECT","INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER"):
                assert not connection.execute(text("SELECT has_table_privilege(:app,:table,:privilege)"),
                    {"app":app,"table":"public."+name,"privilege":privilege}).scalar_one()
        connection.exec_driver_sql("CREATE TABLE public.c02_acl_probe (id integer)")
        connection.exec_driver_sql("CREATE SEQUENCE public.c02_acl_sequence")
        assert not connection.execute(text("SELECT has_table_privilege(:app,'public.c02_acl_probe','SELECT,INSERT,UPDATE,DELETE')"),{"app":app}).scalar_one()
        assert not connection.execute(text("SELECT has_sequence_privilege(:app,'public.c02_acl_sequence','USAGE,SELECT,UPDATE')"),{"app":app}).scalar_one()
        connection.rollback()  # probe DDL is never committed


@contextmanager
def owner_rollback(owner):
    with owner.connect() as connection:
        transaction = connection.begin()
        try:yield connection
        finally:transaction.rollback()


@pytest.mark.parametrize("kind,email,password,display,valid",[
    ("email","user@example.com","$argon2id$fixture-only","사용자",True),
    ("developer",None,None,"개발자",True),
    ("email",None,"fixture","사용자",False),("email","user@example.com",None,"사용자",False),
    ("email","USER@example.com","fixture","사용자",False),("email"," user@example.com","fixture","사용자",False),
    ("email","사용자@example.com","fixture","사용자",False),("email","user@example.com","  ","사용자",False),
    ("email","user@example.com","fixture"," \t",False),("developer","fake@example.com",None,"개발자",False),
    ("developer",None,"fake-hash","개발자",False),("unknown",None,None,"사용자",False),
])
def test_account_domain_conditions(migrated,kind,email,password,display,valid):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    _,owner,_=migrated
    def write(connection):
        connection.execute(text("INSERT INTO public.users (user_id,account_kind,email,password_hash,display_name) VALUES (:id,:kind,:email,:password,:display)"),
            {"id":uuid4(),"kind":kind,"email":email,"password":password,"display":display})
    with owner_rollback(owner) as connection:
        if valid:write(connection)
        else:
            with pytest.raises(IntegrityError) as caught:write(connection)
            assert caught.value.orig.sqlstate=="23514"


@pytest.mark.parametrize("table,values,sqlstate",[
    ("users",{"display_name":"valid","account_kind":"developer","created_at":"infinity"},"23514"),
    ("users",{"display_name":"valid","account_kind":"developer","updated_at":"2000-01-01T00:00:00Z"},"23514"),
    ("user_sessions",{"user_id":str(uuid4()),"token_hash":"A"*64,"expires_at":"2099-01-01T00:00:00Z"},"23514"),
    ("user_sessions",{"user_id":str(uuid4()),"token_hash":"a"*64,"expires_at":"2000-01-01T00:00:00Z"},"23514"),
    ("user_sessions",{"user_id":str(uuid4()),"token_hash":"b"*64,"expires_at":"infinity"},"23514"),
    ("user_sessions",{"user_id":str(uuid4()),"token_hash":"c"*64,"expires_at":"2099-01-01T00:00:00Z","revoked_at":"2000-01-01T00:00:00Z"},"23514"),
    ("projects",{"created_by":str(uuid4()),"name":" "},"23514"),
    ("projects",{"created_by":str(uuid4()),"name":"valid","business_state":"unknown"},"23514"),
    ("projects",{"created_by":str(uuid4()),"name":"valid","business_state":"archived"},"23514"),
    ("projects",{"created_by":str(uuid4()),"name":"valid","archived_at":"2099-01-01T00:00:00Z"},"23514"),
    ("projects",{"created_by":str(uuid4()),"name":"valid","due_at":"-infinity"},"23514"),
    ("projects",{"created_by":str(uuid4()),"name":"valid","use_channels":"{}"},"23514"),
    ("projects",{"created_by":str(uuid4()),"name":"valid","use_channels":"null"},"23514"),
    ("projects",{"created_by":str(uuid4()),"name":"valid","use_channels":"[1]"},"23514"),
    ("projects",{"created_by":str(uuid4()),"name":"valid","use_channels":"[null]"},"23514"),
    ("project_members",{"user_id":str(uuid4()),"role":"admin"},"23514"),
    ("project_members",{"user_id":str(uuid4()),"updated_at":"infinity"},"23514"),
])
def test_single_row_checks(migrated,table,values,sqlstate):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    _,owner,_=migrated
    key={"users":"user_id","user_sessions":"session_id","projects":"project_id","project_members":"project_id"}[table]
    body={key:str(uuid4()),**values}
    sql=f'INSERT INTO public."{table}" ('+','.join('"'+k+'"' for k in body)+') VALUES ('+','.join(':'+k for k in body)+')'
    with owner_rollback(owner) as connection:
        with pytest.raises(IntegrityError) as caught:connection.execute(text(sql),body)
        assert caught.value.orig.sqlstate==sqlstate


def test_valid_defaults_multiple_sessions_and_duplicate_project_names(migrated):
    from sqlalchemy import text
    _,owner,_=migrated
    with owner_rollback(owner) as connection:
        user=uuid4();project=uuid4()
        row=connection.execute(text("INSERT INTO public.users (user_id,email,password_hash,display_name) VALUES (:id,'person@example.com','fixture-only','사람') RETURNING account_kind,is_active,created_at,updated_at"),{"id":user}).one()
        assert row[0:2]==("email",True) and row[2]==row[3]
        for i in range(2):
            connection.execute(text("INSERT INTO public.user_sessions (session_id,user_id,token_hash,expires_at) VALUES (:id,:user,:hash,CURRENT_TIMESTAMP + interval '8 hours')"),{"id":uuid4(),"user":user,"hash":str(i)*64})
            values=connection.execute(text("INSERT INTO public.projects (project_id,created_by,name,use_channels) VALUES (:id,:user,'same name',:channels) RETURNING business_state,use_channels"),{"id":project if i==0 else uuid4(),"user":user,"channels":'[]' if i==0 else '["웹","인쇄"]'}).one()
            assert values[0]=="active" and isinstance(values[1],list)
        role=connection.execute(text("INSERT INTO public.project_members (project_id,user_id) VALUES (:project,:user) RETURNING role,is_active"),{"project":project,"user":user}).one()
        assert role==("viewer",True)


@pytest.mark.parametrize("entity",["email","token","member","user_pk"])
def test_unique_and_primary_keys(migrated,entity):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    _,owner,_=migrated
    user,project=uuid4(),uuid4()
    with owner_rollback(owner) as connection:
        if entity in {"email","user_pk"}:
            statement="INSERT INTO public.users (user_id,email,password_hash,display_name) VALUES (:id,:email,'fixture','name')"
            connection.execute(text(statement),{"id":user,"email":"unique@example.com"})
            body={"id":user if entity=="user_pk" else uuid4(),"email":"another@example.com" if entity=="user_pk" else "unique@example.com"}
        elif entity=="token":
            statement="INSERT INTO public.user_sessions (session_id,user_id,token_hash,expires_at) VALUES (:id,:user,:hash,CURRENT_TIMESTAMP+interval '8 hours')"
            body={"id":uuid4(),"user":user,"hash":"f"*64};connection.execute(text(statement),body);body={**body,"id":uuid4()}
        else:
            statement="INSERT INTO public.project_members (project_id,user_id) VALUES (:project,:user)"
            body={"project":project,"user":user};connection.execute(text(statement),body)
        with pytest.raises(IntegrityError) as caught:connection.execute(text(statement),body)
        assert caught.value.orig.sqlstate=="23505"


def test_repeat_upgrade_and_destructive_downgrade_guard(migrated):
    from alembic import command
    from alembic.util import CommandError
    database,owner,configuration=migrated
    command.upgrade(configuration(),"M001")
    with pytest.raises(CommandError,match="destructive downgrade is disabled"):
        command.downgrade(configuration(),"base")
    with owner.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one()=="M001"


def test_app_role_cannot_run_owner_migration(migrated):
    from alembic import command
    from alembic.util import CommandError
    from backend.app.config import Secret
    database,_,configuration=migrated
    config=configuration();settings=config.attributes["migration_settings"]
    env=database.environment
    config.attributes["migration_settings"]=replace(settings,owner_user=settings.app_user,
        owner_password=Secret(env["TEST_DB_APP_PASSWORD"]))
    with pytest.raises(CommandError,match="requires the expected"):
        command.upgrade(config,"M001")
