"""M008 policy is exercised using the actual restricted app connection."""
from pathlib import Path
from uuid import uuid4
import pytest

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_guard_migration_integration import guards_database  # noqa: F401
from backend.tests.guard_fixtures import create_guard_graph,insert_row

pytestmark=pytest.mark.db_integration


@pytest.fixture(scope='module')
def grants_database(guards_database):
    from alembic import command
    db,owner,configuration,main,other=guards_database
    command.upgrade(configuration(),'M008')
    return db,owner,configuration,main,other


def test_app_can_commit_full_graph_with_owner_registered_schema(grants_database):
    db,owner,_,main,_=grants_database
    with db.engine.begin() as connection:
        user=insert_row(connection,'users',{'user_id':uuid4(),'account_kind':'email','email':uuid4().hex+'@example.invalid',
            'password_hash':'fixture-only-not-a-real-password','display_name':'M008 synthetic user'})
        graph=create_guard_graph(connection,user=user,schema=main['schema_definitions'])
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT count(*) FROM public.run_item_selections').scalar_one()==3
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one()=='M008'


@pytest.fixture(scope='module')
def app_graph(grants_database):
    db,_,_,g,_=grants_database
    with db.engine.begin() as connection:
        user=insert_row(connection,'users',{'user_id':uuid4(),'account_kind':'email','email':uuid4().hex+'@example.invalid',
            'password_hash':'fixture-only-not-a-real-password','display_name':'M008 second synthetic user'})
        graph=create_guard_graph(connection,user=user,schema=g['schema_definitions'])
    return graph


def test_actual_app_catalog_matches_complete_reviewed_inventory(grants_database,request):
    import json
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from backend.tests.schema_helpers import metadata_with_default_schema_alias
    from backend.app.persistence.schema_inventory import check_schema,capture_structure,capture_privileges,_load_contract
    db,owner,_,_,_=grants_database
    result=check_schema(db.engine,db.settings)
    assert result['status']=='schema_ready' and result['revision']=='M008'
    request.node.user_properties.append(('m008_readiness',json.dumps(result)))
    with db.engine.connect() as connection:
        actual=capture_structure(connection);privileges=capture_privileges(connection,db.settings.database.username)
        assert actual==_load_contract()['structure'] and privileges==_load_contract()['privileges']
        request.node.user_properties.append(('m008_privilege_counts',json.dumps({
            'select_tables':sum(r[1]=='SELECT' and r[2] for r in privileges['tables']),
            'insert_tables':sum(r[1]=='INSERT' and r[2] for r in privileges['tables']),
            'table_update':sum(r[1]=='UPDATE' and r[2] for r in privileges['tables']),
            'column_update':sum(r[2]=='UPDATE' and r[3] for r in privileges['columns']),
            'execute_functions':sum(r[1] for r in privileges['functions']),
            'public_grants':len(privileges['public_grants']),'default_grants':len(privileges['default_grants'])})))
    with owner.connect() as connection:
        differences=compare_metadata(MigrationContext.configure(connection,opts={
            'include_object':lambda obj,name,kind,reflected,compare_to:kind!='table' or name!='alembic_version',
            'compare_server_default':True}),metadata_with_default_schema_alias(include_indexes=True))
        assert differences==[],differences


from backend.tests.grant_fixtures import update_policy
from backend.app.persistence.schema import metadata
from backend.tests.test_identity_migration_integration import owner_rollback
from backend.tests.guard_fixtures import update_row,clone_ocr,clone_extraction,clone_criteria
from sqlalchemy.exc import DBAPIError

TABLES=sorted(t.name for t in metadata.tables.values())


def denied(connection,sql,*,state='42501'):
    with connection.begin_nested() as transaction:
        with pytest.raises(DBAPIError) as error:connection.exec_driver_sql(sql)
        assert error.value.orig.sqlstate==state
        transaction.rollback()


@pytest.mark.parametrize('table',TABLES)
def test_every_immutable_column_and_destructive_right_is_denied(grants_database,table):
    db,*_=grants_database
    allowed=update_policy().get(table,())
    with db.engine.connect() as connection:
        for column in metadata.tables['public.'+table].c:
            if column.name not in allowed:
                denied(connection,f'UPDATE public."{table}" SET "{column.name}"="{column.name}" WHERE false')
        denied(connection,f'DELETE FROM public."{table}" WHERE false')
        denied(connection,f'TRUNCATE public."{table}"')
        assert connection.exec_driver_sql(f'SELECT * FROM public."{table}" LIMIT 1').first() is not None


@pytest.mark.parametrize('table',sorted(update_policy()))
def test_real_app_updates_on_all_14_mutable_tables(grants_database,app_graph,table):
    from datetime import datetime,timezone
    db,*_=grants_database;g=app_graph
    with owner_rollback(db.engine) as connection:
        if table=='ocr_runs':
            row=clone_ocr(connection,g);update_row(connection,table,row,sealed_at=datetime.now(timezone.utc))
        elif table=='poster_extractions':
            row=clone_extraction(connection,g);update_row(connection,table,row,sealed_at=datetime.now(timezone.utc))
        elif table=='criteria_versions':clone_criteria(connection,g)
        elif table=='review_runs':update_row(connection,table,dict(g['active_run']),progress=51)
        elif table=='review_attempts':
            connection.exec_driver_sql('SELECT 1 FROM public.review_runs WHERE run_id=%s FOR UPDATE',(g['active_run']['run_id'],))
            update_row(connection,table,dict(g['active_attempt']),actual_runtime={'fixture':'updated'})
        elif table=='criteria_drafts':update_row(connection,table,dict(g[table]),revision=2,raw_requirements='C07c updated synthetic requirements')
        else:
            changes={'users':{'display_name':'C07c updated'},'user_sessions':{'revoked_at':datetime.now(timezone.utc)},
                'projects':{'name':'C07c renamed'},'project_members':{'is_active':True},'assets':{'name':'C07c asset'},
                'stored_files':{'state':'missing'},'import_packages':{'import_state':'needs_review'},'criteria_sets':{'name':'C07c criteria'}}
            from backend.tests.guard_fixtures import row_where
            target=metadata.tables['public.'+table]
            connection.execute(target.update().where(row_where(target,g[table])).values(**changes[table]))
        connection.exec_driver_sql('SET CONSTRAINTS ALL IMMEDIATE')


@pytest.mark.parametrize('sql',[
 'INSERT INTO public.schema_definitions DEFAULT VALUES',
 'UPDATE public.alembic_version SET version_num=version_num',
 'DELETE FROM public.alembic_version',
 'CREATE TABLE public.c07c_forbidden(id integer)',
 'CREATE TEMP TABLE c07c_forbidden(id integer)',
 'CREATE SCHEMA c07c_forbidden',
 'ALTER TABLE public.users ADD COLUMN c07c_forbidden integer',
 'ALTER TABLE public.users DISABLE TRIGGER USER',
 'DROP TABLE public.audit_events',
 'DROP FUNCTION public.cc_fail(text)',
 "CREATE OR REPLACE FUNCTION public.cc_fail(text) RETURNS void LANGUAGE plpgsql AS $$ BEGIN RETURN; END $$",
 'CREATE TRIGGER c07c_forbidden BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.cc_transition()',
 'SELECT public.cc_transition()', 'SELECT public.cc_append_gate()',
 'SELECT public.cc_stored_import_gate()', 'SELECT public.cc_snapshot_guard()',
 'SELECT public.cc_schema_files(NULL::uuid,NULL::uuid,NULL::text)',
 'SET session_replication_role=replica',
])
def test_app_cannot_manage_schema_catalog_or_bypass_guards(grants_database,sql):
    db,*_=grants_database
    with db.engine.connect() as connection:denied(connection,sql)


def test_app_cannot_set_owner_role_or_delegate_its_rights(grants_database):
    from sqlalchemy import text
    db,owner,_,_,_=grants_database
    with db.engine.connect() as connection:
        with owner.connect() as own:role=own.exec_driver_sql('SELECT current_user').scalar_one()
        denied(connection,'SET ROLE '+connection.dialect.identifier_preparer.quote(role))
        # PostgreSQL may warn rather than raise for a GRANT without grant option.
        connection.exec_driver_sql('GRANT SELECT ON public.users TO PUBLIC')
        assert not connection.execute(text("SELECT has_table_privilege(:role,'public.users','SELECT WITH GRANT OPTION')"),{'role':db.settings.database.username}).scalar_one()
        assert connection.exec_driver_sql("SELECT count(*) FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE c.oid='public.users'::regclass AND a.grantee=0").scalar_one()==0


@pytest.mark.parametrize('table,column',[
 ('ocr_runs','sealed_at'),('poster_extractions','sealed_at'),('criteria_versions','sealed_at'),
 ('review_runs','overall_verdict'),('review_attempts','stage')])
def test_allowed_column_does_not_allow_editing_completed_snapshot(grants_database,app_graph,table,column):
    from backend.tests.guard_fixtures import row_where
    db,*_=grants_database
    target=metadata.tables['public.'+table]
    with owner_rollback(db.engine) as connection:
        with pytest.raises(DBAPIError) as error:
            connection.execute(target.update().where(row_where(target,app_graph[table])).values({column:app_graph[table][column]}))
        assert error.value.orig.sqlstate=='23514'


def test_actual_app_commit_failure_rolls_back_entire_graph_and_audit(grants_database,app_graph):
    from backend.app.persistence.transaction import unit_of_work
    from backend.app.persistence.database import PersistenceError
    db,*_=grants_database;g=app_graph;token=uuid4()
    with pytest.raises(PersistenceError) as error:
        with unit_of_work(db.engine) as connection:
            row=clone_ocr(connection,g)  # Deliberately leave the parent unsealed at COMMIT.
            insert_row(connection,'ocr_tokens',{**g['ocr_tokens'],'token_id':token,'ocr_run_id':row['ocr_run_id']})
            audit=insert_row(connection,'audit_events',{**g['audit_events'],'event_id':uuid4(),'action':'c07c.rollback'})
    assert error.value.code=='DB_CONSTRAINT_VIOLATION' and error.value.sqlstate=='23514' and error.value.outcome=='rolled_back'
    with db.engine.connect() as connection:
        for table,key,value in [('ocr_runs','ocr_run_id',row['ocr_run_id']),('ocr_tokens','token_id',token),('audit_events','event_id',audit['event_id'])]:
            assert connection.exec_driver_sql(f'SELECT count(*) FROM public.{table} WHERE {key}=%s',(value,)).scalar_one()==0


def test_future_owner_objects_have_no_implicit_app_or_public_rights(grants_database):
    db,owner,*_=grants_database
    with owner.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE public.c07c_future(id integer)')
        connection.exec_driver_sql('CREATE SEQUENCE public.c07c_future_seq')
        connection.exec_driver_sql('CREATE FUNCTION public.c07c_future_fn() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$')
    try:
        with db.engine.connect() as connection:
            for sql in ('SELECT * FROM public.c07c_future',"SELECT nextval('public.c07c_future_seq')",'SELECT public.c07c_future_fn()'):
                denied(connection,sql)
    finally:
        with owner.begin() as connection:
            connection.exec_driver_sql('DROP TABLE public.c07c_future')
            connection.exec_driver_sql('DROP SEQUENCE public.c07c_future_seq')
            connection.exec_driver_sql('DROP FUNCTION public.c07c_future_fn()')


STRUCTURE_DRIFT=[
 'ALTER TABLE public.user_sessions DROP CONSTRAINT fk_user_sessions_user; ALTER TABLE public.user_sessions ADD CONSTRAINT fk_user_sessions_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON UPDATE RESTRICT ON DELETE CASCADE',
 'ALTER TABLE public.users ALTER COLUMN is_active SET DEFAULT false',
 'ALTER TABLE public.users ALTER COLUMN display_name TYPE varchar(500)',
 'ALTER TABLE public.users ALTER COLUMN display_name DROP NOT NULL',
 'ALTER TABLE public.users DROP CONSTRAINT ck_users_times; ALTER TABLE public.users ADD CONSTRAINT ck_users_times CHECK (isfinite(created_at) AND isfinite(updated_at) AND updated_at >= created_at AND true)',
 'ALTER TABLE public.users RENAME CONSTRAINT uq_users_email TO uq_users_email_changed',
 'ALTER TABLE public.users DISABLE TRIGGER ct_project_owner',
 'ALTER TABLE public.users ENABLE ALWAYS TRIGGER ct_project_owner',
 'CREATE OR REPLACE FUNCTION public.cc_fail(rule_name text) RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$ BEGIN RETURN; END $$',
 'ALTER FUNCTION public.cc_fail(text) SECURITY DEFINER',
 'ALTER FUNCTION public.cc_fail(text) SET search_path=public',
 'CREATE INDEX c07c_extra ON public.users(display_name)',
 'DROP INDEX public.ix_assets_parent; CREATE INDEX ix_assets_parent ON public.assets(project_id,parent_asset_id)',
 'DROP INDEX public.ix_assets_project_recent; CREATE INDEX ix_assets_project_recent ON public.assets(project_id,created_at ASC,asset_id DESC)',
 'ALTER TABLE public.users ALTER COLUMN display_name TYPE varchar(120) COLLATE "C"',
 'CREATE TABLE public.c07c_extra(id integer)',
 'ALTER TABLE public.users ENABLE ROW LEVEL SECURITY',
 'CREATE POLICY c07c_extra ON public.users USING (true)',
]


@pytest.mark.parametrize('ddl',STRUCTURE_DRIFT)
def test_same_revision_with_structural_drift_is_rejected_without_repair(grants_database,ddl):
    from backend.app.persistence.schema_inventory import validate_schema,capture_structure
    from backend.app.persistence.database import PersistenceError
    db,owner,*_=grants_database
    with owner_rollback(owner) as connection:
        connection.exec_driver_sql(ddl)
        changed=capture_structure(connection)
        assert connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one()=='M008'
        with pytest.raises(PersistenceError) as error:validate_schema(connection,app_user=db.settings.database.username)
        assert error.value.code=='DB_SCHEMA_DRIFT'
        assert capture_structure(connection)==changed


PRIVILEGE_DRIFT=[
 'GRANT UPDATE ON public.users TO {app}',
 'GRANT UPDATE (created_at) ON public.users TO {app}',
 'GRANT DELETE ON public.audit_events TO {app}',
 'GRANT INSERT ON public.schema_definitions TO {app}',
 'GRANT SELECT ON public.users TO {app} WITH GRANT OPTION',
 'GRANT SELECT ON public.users TO PUBLIC',
 'GRANT UPDATE (created_at) ON public.users TO PUBLIC',
 'GRANT EXECUTE ON FUNCTION public.cc_schema_files(uuid,uuid,text) TO {app}',
 'GRANT EXECUTE ON FUNCTION public.cc_fail(text) TO PUBLIC',
 'REVOKE EXECUTE ON FUNCTION public.cc_lock_project(uuid) FROM {app}',
 'REVOKE SELECT ON public.alembic_version FROM {app}',
 'GRANT CREATE ON SCHEMA public TO {app}',
 'GRANT USAGE ON SCHEMA public TO {app} WITH GRANT OPTION',
 'GRANT TEMPORARY ON DATABASE {db} TO PUBLIC',
 'GRANT CONNECT ON DATABASE {db} TO {app} WITH GRANT OPTION',
 'ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO {app}',
 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO PUBLIC',
]


@pytest.mark.parametrize('ddl',PRIVILEGE_DRIFT)
def test_same_revision_with_privilege_drift_is_rejected_without_repair(grants_database,ddl):
    from backend.app.persistence.schema_inventory import validate_schema,capture_privileges
    from backend.app.persistence.database import PersistenceError
    db,owner,*_=grants_database;quote=owner.dialect.identifier_preparer.quote
    statement=ddl.format(app=quote(db.settings.database.username),db=quote(db.name))
    with owner_rollback(owner) as connection:
        connection.exec_driver_sql(statement)
        changed=capture_privileges(connection,db.settings.database.username)
        with pytest.raises(PersistenceError) as error:validate_schema(connection,app_user=db.settings.database.username)
        assert error.value.code=='DB_PRIVILEGES_INVALID'
        assert capture_privileges(connection,db.settings.database.username)==changed


def test_readiness_transaction_is_read_only_and_app_identity_is_checked(grants_database,monkeypatch):
    from backend.app.persistence import schema_inventory as inventory
    from backend.app.persistence.database import PersistenceError
    db,owner,*_=grants_database;original=inventory.capture_structure;observed=[]
    def capture(connection):
        observed.append(connection.exec_driver_sql("SELECT current_user,current_setting('transaction_isolation'),current_setting('transaction_read_only'),pg_current_xact_id_if_assigned() IS NULL").one())
        return original(connection)
    monkeypatch.setattr(inventory,'capture_structure',capture)
    assert inventory.check_schema(db.engine,db.settings)['status']=='schema_ready'
    assert observed==[(db.settings.database.username,'repeatable read','on',True)]
    with pytest.raises(PersistenceError) as error:inventory.check_schema(owner,db.settings)
    assert error.value.code=='DB_ROLE_REJECTED'


def test_cli_uses_only_explicit_app_settings_and_prints_safe_summary(grants_database):
    import json,os,subprocess,sys
    db,*_=grants_database
    environment=os.environ.copy();environment.update({'PERSISTENCE_MODE':'postgres','APP_ENV':'development',
        'DB_HOST':db.environment['TEST_DB_HOST'],'DB_PORT':db.environment['TEST_DB_PORT'],
        'POSTGRES_DB':db.name,'DB_APP_USER':db.settings.database.username,
        'DB_APP_PASSWORD':db.settings.database.password.reveal()})
    result=subprocess.run([sys.executable,'-m','scripts.check_schema'],env=environment,capture_output=True,text=True)
    assert result.returncode==0,result.stdout
    assert json.loads(result.stdout)['status']=='schema_ready'
    assert db.settings.database.password.reveal() not in result.stdout+result.stderr


def test_app_concurrent_owner_demotions_preserve_one_active_owner(grants_database,app_graph):
    from sqlalchemy import delete,text
    from backend.tests.test_guard_concurrency_integration import compete
    db,owner,*_=grants_database;g=app_graph
    with db.engine.begin() as connection:
        user=insert_row(connection,'users',{'user_id':uuid4(),'account_kind':'email','email':uuid4().hex+'@example.invalid',
            'password_hash':'fixture-only','display_name':'C07c competing owner'})
        member=insert_row(connection,'project_members',{'project_id':g['projects']['project_id'],'user_id':user['user_id'],'role':'owner'})
    try:
        operations=[lambda connection,row=dict(row):update_row(connection,'project_members',row,role='viewer')
            for row in (g['project_members'],member)]
        assert sorted(compete(db.engine,operations))==['committed','ct_project_owner']
        with db.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM public.project_members m JOIN public.users u USING(user_id) WHERE m.project_id=:project AND m.role='owner' AND m.is_active AND u.is_active"),{'project':g['projects']['project_id']}).scalar_one()==1
    finally:
        with owner.begin() as connection:
            update_row(connection,'project_members',dict(g['project_members']),role='owner')
            connection.execute(delete(metadata.tables['public.project_members']).where(metadata.tables['public.project_members'].c.user_id==user['user_id']))
            connection.execute(delete(metadata.tables['public.users']).where(metadata.tables['public.users'].c.user_id==user['user_id']))


def test_app_late_result_observes_completion_lock_and_is_rejected(grants_database,app_graph):
    from backend.tests.guard_fixtures import start_run
    from backend.tests.test_guard_concurrency_integration import test_late_result_waits_for_completion_lock_then_rejects_sealed_run
    db,_,config,*_=grants_database
    with db.engine.begin() as connection:run,attempt=start_run(connection,app_graph)
    graph={**app_graph,'active_run':run,'active_attempt':attempt}
    # Reuse the existing lock-observation protocol, now with the restricted app engine.
    test_late_result_waits_for_completion_lock_then_rejects_sealed_run((db,db.engine,config,graph,None))
