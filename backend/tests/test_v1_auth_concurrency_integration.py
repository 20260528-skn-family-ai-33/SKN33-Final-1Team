"""Actual reset/login and permission/short-write lock ordering, with observed blocking."""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import threading,time
import pytest
from sqlalchemy import select,func
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_v1_auth_integration import auth_database,email_user  # noqa: F401
from backend.tests.test_v1_authorization_integration import project,service,subject  # noqa: F401
from backend.tests.auth_fixtures import PASSWORD
from backend.app.contracts.auth import Credentials
from backend.app.security.errors import AuthError
from backend.app.services.authorization import Authorization
from backend.app.persistence.transaction import unit_of_work
from backend.app.persistence.schema.ocr import audit_events

pytestmark=pytest.mark.db_integration


def blocked(owner,pid):
    deadline=time.monotonic()+3
    with owner.connect() as connection:
        while time.monotonic()<deadline:
            if connection.exec_driver_sql('SELECT pg_blocking_pids(%s)',(pid,)).scalar_one():return True
            time.sleep(.01)
    return False


def test_password_reset_between_verification_and_session_commit_rejects_stale_login(auth_database,project,monkeypatch):
    db,owner,manager,settings,_=auth_database;_,user=project
    auth=service(auth_database);verified=threading.Event();release=threading.Event();original=auth.passwords.verify
    def paused(password,encoded):
        result=original(password,encoded);verified.set();assert release.wait(timeout=5);return result
    monkeypatch.setattr(auth.passwords,'verify',paused)
    def login():
        try:return auth.login(Credentials(email=user['email'],password=PASSWORD),None,ip='synthetic',developer_allowed=False,request_id='synthetic')
        except AuthError as error:return error.code
    with ThreadPoolExecutor(max_workers=1) as worker:
        future=worker.submit(login)
        try:
            assert verified.wait(timeout=3)
            manager.reset_password(UUID(user['id']),PASSWORD+' changed')
        finally:release.set()
        assert future.result(timeout=5)=='INVALID_CREDENTIALS'
    with owner.connect() as connection:
        assert connection.exec_driver_sql('SELECT count(*) FROM public.user_sessions WHERE user_id=%s AND revoked_at IS NULL',(UUID(user['id']),)).scalar_one()==0


def test_login_commit_before_waiting_password_reset_is_then_revoked(auth_database,project,monkeypatch):
    from backend.app.persistence.repositories import auth as repository
    db,owner,manager,settings,_=auth_database;_,user=project
    auth=service(auth_database);inserted=threading.Event();release=threading.Event();manager_wait=threading.Event();state={}
    original_insert=repository.new_session;original_locks=manager._locks
    def insert(connection,*args):
        result=original_insert(connection,*args);inserted.set();assert release.wait(timeout=5);return result
    def locks(connection,*args,**kwargs):
        state['pid']=connection.exec_driver_sql('SELECT pg_backend_pid()').scalar_one();manager_wait.set();return original_locks(connection,*args,**kwargs)
    monkeypatch.setattr(repository,'new_session',insert);monkeypatch.setattr(manager,'_locks',locks)
    with ThreadPoolExecutor(max_workers=2) as workers:
        future=workers.submit(auth.login,Credentials(email=user['email'],password=PASSWORD),None,ip='synthetic',developer_allowed=False,request_id='synthetic')
        try:
            assert inserted.wait(timeout=3)
            reset=workers.submit(manager.reset_password,UUID(user['id']),PASSWORD+' changed')
            assert manager_wait.wait(timeout=3) and blocked(owner,state['pid'])
        finally:release.set()
        _,raw=future.result(timeout=5);reset.result(timeout=5)
    assert auth.current(raw) is None


@pytest.mark.parametrize('change',('membership','user'))
def test_authorized_short_write_finishes_then_new_write_rechecks_changed_permission(auth_database,project,change,monkeypatch):
    db,owner,manager,settings,_=auth_database;pid,_=project
    writer=email_user(auth_database);uid=UUID(writer['id']);manager.set_project_member(pid,uid,'editor',True)
    principal,_=subject(auth_database,writer);authorization=Authorization(settings)
    ready=threading.Event();state={};original=manager._locks
    def locks(connection,*args,**kwargs):
        state['pid']=connection.exec_driver_sql('SELECT pg_backend_pid()').scalar_one();ready.set();return original(connection,*args,**kwargs)
    monkeypatch.setattr(manager,'_locks',locks)
    with ThreadPoolExecutor(max_workers=1) as worker:
        with unit_of_work(db.engine) as connection:
            authorization.project(connection,principal,pid,action='write')
            operation=(lambda:manager.set_project_member(pid,uid,'viewer',True)) if change=='membership' else (lambda:manager.set_user_active(uid,False))
            future=worker.submit(operation)
            assert ready.wait(timeout=3) and blocked(owner,state['pid'])
            connection.execute(audit_events.insert().values(event_id=uuid4(),project_id=pid,actor_kind='user',actor_user_id=uid,action='synthetic.authorized_write',target_type='project',target_id=pid))
        future.result(timeout=5)
    with unit_of_work(db.engine) as connection:
        with pytest.raises(AuthError) as error:authorization.project(connection,principal,pid,action='write')
        assert error.value.status==(403 if change=='membership' else 401)


def test_cross_user_cookie_rotation_uses_sorted_user_locks(auth_database):
    from backend.app.services.auth import token_hash
    db,owner,manager,settings,_=auth_database
    people=[email_user(auth_database),email_user(auth_database)]
    old=[subject(auth_database,user)[1] for user in people]
    barrier=threading.Barrier(2)
    def swap(index):
        auth=service(auth_database);target=people[1-index]
        barrier.wait(timeout=5)
        user,raw=auth.login(Credentials(email=target['email'],password=PASSWORD),old[index],ip='synthetic',developer_allowed=False,request_id='synthetic')
        return user,raw
    with ThreadPoolExecutor(max_workers=2) as workers:results=list(workers.map(swap,range(2)))
    for index,(user,raw) in enumerate(results):
        assert user['id']==people[1-index]['id'] and service(auth_database).current(raw).public()==user
    assert all(service(auth_database).current(raw) is None for raw in old)
