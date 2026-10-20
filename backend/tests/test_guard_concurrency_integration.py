"""Two real owner transactions compete; no sleep-based assumed lock ordering."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import threading
import time
from uuid import uuid4

import pytest
from sqlalchemy import delete,select,text
from sqlalchemy.exc import DBAPIError
from backend.app.persistence.schema import metadata
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_guard_migration_integration import guards_database  # noqa: F401
from backend.tests.guard_fixtures import insert_row,update_row,finish_run

pytestmark=pytest.mark.db_integration


def compete(owner,operations):
    barrier=threading.Barrier(2)
    def write(operation):
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql("SET LOCAL lock_timeout='5s'")
                connection.exec_driver_sql("SET LOCAL statement_timeout='10s'")
                operation(connection)
                barrier.wait(timeout=5)
            return 'committed'
        except DBAPIError as error:
            assert error.orig.sqlstate=='23514'
            return error.orig.diag.constraint_name
    with ThreadPoolExecutor(max_workers=2) as workers:return list(workers.map(write,operations))


@pytest.mark.parametrize('operation',('membership','user'))
def test_two_owners_cannot_both_remove_their_membership(guards_database,operation):
    _,owner,_,g,_=guards_database
    with owner.begin() as connection:
        user=insert_row(connection,'users',{'user_id':uuid4(),'account_kind':'email','email':uuid4().hex+'@example.invalid',
            'password_hash':'fixture-only','display_name':'second synthetic owner'})
        member=insert_row(connection,'project_members',{'project_id':g['projects']['project_id'],'user_id':user['user_id'],'role':'owner'})
    try:
        operations=([lambda c,row=dict(row):update_row(c,'project_members',row,role='viewer') for row in (g['project_members'],member)]
                    if operation=='membership' else [lambda c,row=dict(row):update_row(c,'users',row,is_active=False) for row in (g['users'],user)])
        assert sorted(compete(owner,operations))==['committed','ct_project_owner']
        with owner.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM public.project_members WHERE project_id=:project AND role='owner' AND is_active AND user_id IN (SELECT user_id FROM public.users WHERE is_active)"),{'project':g['projects']['project_id']}).scalar_one()==1
    finally:
        with owner.begin() as connection:
            update_row(connection,'users',dict(g['users']),is_active=True)
            update_row(connection,'project_members',dict(g['project_members']),role='owner')
            connection.execute(delete(metadata.tables['public.project_members']).where(metadata.tables['public.project_members'].c.user_id==user['user_id']))
            connection.execute(delete(metadata.tables['public.users']).where(metadata.tables['public.users'].c.user_id==user['user_id']))


def test_mutual_parent_edits_serialize_and_reject_second_cycle(guards_database):
    _,owner,_,g,_=guards_database
    with owner.begin() as connection:
        rows=[insert_row(connection,'assets',{**g['assets'],'asset_id':uuid4(),'name':'concurrent synthetic asset'}) for _ in range(2)]
    try:
        operations=[lambda c,i=i:update_row(c,'assets',dict(rows[i]),parent_asset_id=rows[1-i]['asset_id']) for i in range(2)]
        assert sorted(compete(owner,operations))==['committed','ct_lineage_assets']
    finally:
        with owner.begin() as connection:
            for row in rows:update_row(connection,'assets',row,parent_asset_id=None)
            connection.execute(delete(metadata.tables['public.assets']).where(metadata.tables['public.assets'].c.asset_id.in_([r['asset_id'] for r in rows])))


def test_late_result_waits_for_completion_lock_then_rejects_sealed_run(guards_database):
    _,owner,_,g,_=guards_database
    run=dict(g['active_run']);attempt=dict(g['active_attempt'])
    ready=threading.Event();state={}
    def late_write():
        try:
            with owner.begin() as connection:
                connection.exec_driver_sql("SET LOCAL lock_timeout='5s'")
                state['pid']=connection.exec_driver_sql('SELECT pg_backend_pid()').scalar_one();ready.set()
                insert_row(connection,'item_results',{**g['item_results'],'result_id':uuid4(),
                    'run_id':run['run_id'],'attempt_id':attempt['attempt_id'],'input_manifest_hash':run['input_manifest_hash']})
            return 'unexpected commit'
        except DBAPIError as error:return error.orig.sqlstate,error.orig.diag.constraint_name
    with ThreadPoolExecutor(max_workers=1) as worker:
        with owner.connect() as holder:
            transaction=holder.begin()
            try:
                finish_run(holder,g,run,attempt)
                future=worker.submit(late_write)
                assert ready.wait(timeout=2)
                blocked=False;deadline=time.monotonic()+2
                with owner.connect() as observer:
                    while time.monotonic()<deadline:
                        blocked=bool(observer.execute(text('SELECT pg_blocking_pids(:pid)'),{'pid':state['pid']}).scalar_one())
                        if blocked:break
                        time.sleep(.02)
                assert blocked,'Expected actual parent-row lock contention before releasing completion.'
                transaction.commit()
                assert future.result(timeout=5)==('23514','cc_run_append_context')
            finally:
                if transaction.is_active:transaction.rollback()
