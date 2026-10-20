"""C07a tests apply an explicitly gated index draft only in generated test DBs."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from uuid import uuid4

import pytest

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_relations_migration_integration import relations_database  # noqa: F401
from backend.tests.test_identity_migration_integration import owner_rollback
from backend.tests.relation_fixtures import insert_row, row_where
from backend.tests.index_fixtures import index_contract, normalize_predicate, stage_index_draft

pytestmark=pytest.mark.db_integration
INDEX_CONTRACT=index_contract()


@pytest.fixture(scope='module')
def indexes_database(relations_database,tmp_path_factory):
    from alembic import command
    from alembic.util import CommandError
    from sqlalchemy import delete
    from backend.app.persistence.schema import metadata
    db,owner,configuration,main,other_image,other_project=relations_database
    staged=stage_index_draft(tmp_path_factory.mktemp('c07a_index_draft'))
    def draft_config(directory=staged,enabled=True):
        config=configuration(directory)
        if enabled:config.attributes['allow_c07a_index_draft']=True
        return config
    def snapshot():
        with owner.connect() as connection:
            return (connection.exec_driver_sql("SELECT c.relname,c.oid::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY 1").all(),
                    connection.exec_driver_sql('SELECT defaclobjtype,defaclnamespace,defaclacl::text FROM pg_default_acl ORDER BY 1,2').all(),
                    [connection.exec_driver_sql(f'SELECT to_jsonb(t)::text FROM public."{t.name}" t ORDER BY to_jsonb(t)::text').all() for t in metadata.tables.values()],
                    connection.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one())
    before=snapshot()
    with pytest.raises(CommandError,match='Incomplete M007 index draft'):
        command.upgrade(draft_config(enabled=False),'M007')
    assert snapshot()==before
    # Neither pre-existing invalid duplicates nor name conflicts are silently adopted.
    for kind in ('developer','active_attempt'):
        table=metadata.tables['public.users' if kind=='developer' else 'public.review_attempts']
        row=({'user_id':uuid4(),'account_kind':'developer','display_name':'C07a duplicate fixture'} if kind=='developer'
             else {**main['active_attempt'],'attempt_id':uuid4(),'attempt_no':2})
        with owner.begin() as connection:connection.execute(table.insert().values(**row))
        with_duplicate=snapshot()
        with pytest.raises(CommandError,match='SQLSTATE=23505'):command.upgrade(draft_config(),'M007')
        assert snapshot()==with_duplicate
        with owner.begin() as connection:connection.execute(delete(table).where(row_where(table,row)))
        assert snapshot()==before
    with owner.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE public.ix_review_runs_project_status (marker text)')
        connection.exec_driver_sql("INSERT INTO public.ix_review_runs_project_status VALUES ('preserve original')")
    collision=snapshot()
    with pytest.raises(CommandError,match='SQLSTATE=42P07'):command.upgrade(draft_config(),'M007')
    assert snapshot()==collision
    with owner.begin() as connection:
        assert connection.exec_driver_sql('SELECT marker FROM public.ix_review_runs_project_status').scalar_one()=='preserve original'
        connection.exec_driver_sql('DROP TABLE public.ix_review_runs_project_status')
    assert snapshot()==before
    failed=stage_index_draft(tmp_path_factory.mktemp('c07a_index_failure'))
    revision=failed/'versions/M007_indexes_draft.py'
    revision.write_text(revision.read_text().replace('\n\ndef downgrade():','\n    op.get_bind().exec_driver_sql("SELECT 1 / 0")\n\ndef downgrade():'))
    with pytest.raises(CommandError,match='SQLSTATE=22012'):command.upgrade(draft_config(failed),'M007')
    assert snapshot()==before
    command.upgrade(draft_config(),'M007')
    after=snapshot()
    assert after[1:3]==before[1:3] and after[3]=='M007'
    # Existing relation OIDs remain; only the 27 candidate indexes are added.
    assert set(before[0]).issubset(set(after[0])) and len(after[0])-len(before[0])==27
    return db,owner,draft_config,main,other_image,other_project


@pytest.mark.parametrize('name,table,keys,unique,predicate',INDEX_CONTRACT,ids=[s[0] for s in INDEX_CONTRACT])
def test_each_index_catalog_order_uniqueness_predicate_and_readiness(indexes_database,name,table,keys,unique,predicate):
    from sqlalchemy import text
    _,owner,_,_,_,_=indexes_database
    with owner.connect() as connection:
        row=connection.execute(text("""SELECT t.relname,am.amname,i.indisunique,i.indisvalid,i.indisready,i.indislive,
             i.indnkeyatts,i.indnatts,pg_get_expr(i.indpred,i.indrelid),i.indexprs IS NULL,
             ARRAY(SELECT pg_get_indexdef(i.indexrelid,k,true) FROM generate_series(1,i.indnkeyatts) k),
             ARRAY(SELECT (i.indoption[k-1] & 1)=1 FROM generate_series(1,i.indnkeyatts) k),
             pg_get_indexdef(i.indexrelid)
             FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
             JOIN pg_class t ON t.oid=i.indrelid JOIN pg_am am ON am.oid=c.relam
             WHERE c.oid=to_regclass(:name)"""),{'name':'public.'+name}).one()
        assert row[:8]==(table,'btree',unique,True,True,True,len(keys),len(keys))
        assert normalize_predicate(row[8])==normalize_predicate(predicate)
        assert row[9] is True
        # Per-column pg_get_indexdef returns the expression without sort options.
        # indoption bit 0 records DESC in each zero-based B-tree key slot.
        assert list(row[10])==[col for col,_ in keys]
        assert list(row[11])==[desc for _,desc in keys],row[12]


def test_complete_index_inventory_keeps_schema_data_and_app_privileges(indexes_database):
    from sqlalchemy import text
    from backend.app.persistence.schema import metadata
    db,owner,_,_,_,_=indexes_database
    with owner.connect() as connection:
        names=connection.exec_driver_sql("SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relnamespace='public'::regnamespace AND NOT EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conindid=i.indexrelid AND k.contype IN ('p','u'))").scalars().all()
        assert set(names)=={s[0] for s in INDEX_CONTRACT} and len(names)==27
        assert connection.exec_driver_sql("SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relnamespace='public'::regnamespace AND i.indrelid<>'public.alembic_version'::regclass").scalar_one()==94
        assert connection.exec_driver_sql("SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND contype='f' AND convalidated AND NOT condeferrable").scalar_one()==81
        for table in metadata.tables.values():
            assert not connection.execute(text("SELECT has_table_privilege(:app,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),{'app':db.settings.database.username,'table':'public.'+table.name}).scalar_one()
    assert sum(len(t.indexes) for t in metadata.tables.values())==27


@pytest.mark.parametrize('inactive',(False,True))
def test_developer_uniqueness_includes_disabled_subject(indexes_database,inactive):
    from sqlalchemy.exc import IntegrityError
    from backend.app.persistence.schema import metadata
    _,owner,_,main,_,_=indexes_database
    table=metadata.tables['public.users']
    with owner_rollback(owner) as connection:
        if inactive:connection.execute(table.update().where(table.c.user_id==main['users']['user_id']).values(is_active=False))
        with pytest.raises(IntegrityError) as caught:
            connection.execute(table.insert().values(user_id=uuid4(),account_kind='developer',display_name='second developer'))
        assert caught.value.orig.sqlstate=='23505' and caught.value.orig.diag.constraint_name=='uq_users_developer_kind'


def test_email_accounts_are_not_subject_to_developer_partial_unique(indexes_database):
    from backend.app.persistence.schema import metadata
    _,owner,_,_,_,_=indexes_database
    with owner_rollback(owner) as connection:
        for i in range(2):
            connection.execute(metadata.tables['public.users'].insert().values(user_id=uuid4(),account_kind='email',
                email=f'c07a-fixture-{i}@example.invalid',password_hash='fixture-only-not-a-real-password',display_name='synthetic email subject'))


@pytest.mark.parametrize('operation',('insert','update'))
def test_one_active_attempt_per_run_rejects_second_insert_or_transition(indexes_database,operation):
    from sqlalchemy.exc import IntegrityError
    from backend.app.persistence.schema import metadata
    _,owner,_,main,_,_=indexes_database
    table=metadata.tables['public.review_attempts']
    body={**main['active_attempt'],'attempt_id':uuid4(),'attempt_no':2}
    with owner_rollback(owner) as connection:
        if operation=='update':
            body.update(execution_status='failed',finished_at=main['review_attempts']['finished_at'])
            connection.execute(table.insert().values(**body))
            stmt=table.update().where(table.c.attempt_id==body['attempt_id']).values(execution_status='active',finished_at=None)
        else:stmt=table.insert().values(**body)
        with pytest.raises(IntegrityError) as caught:connection.execute(stmt)
        assert caught.value.orig.sqlstate=='23505' and caught.value.orig.diag.constraint_name=='uq_review_attempts_active_run'


def test_finished_attempts_allow_new_active_reservation(indexes_database):
    from backend.app.persistence.schema import metadata
    _,owner,_,main,_,_=indexes_database
    table=metadata.tables['public.review_attempts']
    with owner_rollback(owner) as connection:
        connection.execute(table.update().where(table.c.attempt_id==main['active_attempt']['attempt_id']).values(
            execution_status='failed',finished_at=main['review_attempts']['finished_at']))
        connection.execute(table.insert().values(**{**main['active_attempt'],'attempt_id':uuid4(),'attempt_no':2}))


def test_concurrent_active_reservations_commit_only_one(indexes_database):
    from sqlalchemy import delete,select
    from sqlalchemy.exc import IntegrityError
    from backend.app.persistence.schema import metadata
    from backend.tests.test_review_migration_integration import service_run,service_attempt
    db,owner,_,main,_,_=indexes_database
    runs=metadata.tables['public.review_runs'];attempts=metadata.tables['public.review_attempts']
    body={**main['review_runs'],**{c.name:None for c in runs.c if c.nullable},**service_run(),
          'run_id':uuid4(),'requested_by':main['users']['user_id']}
    with owner.begin() as connection:connection.execute(runs.insert().values(**body))
    barrier=threading.Barrier(2)
    def write(number):
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql("SET LOCAL lock_timeout='5s'")
                connection.exec_driver_sql("SET LOCAL statement_timeout='10s'")
                barrier.wait(timeout=5)
                connection.execute(attempts.insert().values(attempt_id=uuid4(),project_id=body['project_id'],
                    asset_version_id=body['asset_version_id'],run_id=body['run_id'],attempt_no=number,**service_attempt()))
            return 'committed'
        except IntegrityError as error:
            assert error.orig.diag.constraint_name=='uq_review_attempts_active_run'
            return error.orig.sqlstate
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:results=list(workers.map(write,(1,2)))
        assert sorted(results)==['23505','committed']
        with owner.connect() as connection:
            assert len(connection.execute(select(attempts.c.attempt_id).where(attempts.c.run_id==body['run_id'])).all())==1
    finally:
        with owner.begin() as connection:
            connection.execute(delete(attempts).where(attempts.c.run_id==body['run_id']))
            connection.execute(delete(runs).where(runs.c.run_id==body['run_id']))


def test_historical_draft_repeat_and_downgrade_guard_keep_official_m007(indexes_database):
    from alembic import command
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError
    from alembic.config import Config
    _,owner,draft_config,_,_,_=indexes_database
    command.upgrade(draft_config(),'M007')
    with pytest.raises(CommandError,match='M007 draft downgrade is disabled'):command.downgrade(draft_config(),'M006')
    assert ScriptDirectory.from_config(Config(str(Path('backend/migrations/alembic.ini').resolve()))).get_heads()==['M008']
