"""Current project/resource authorization and management invariants on real app rows."""
from uuid import UUID,uuid4
from sqlalchemy import select,update,func,delete
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_v1_auth_integration import auth_database,email_user,client,login,csrf  # noqa: F401
from backend.tests.auth_fixtures import PASSWORD,DEV_PROJECT
from backend.app.services.auth import AuthService
from backend.app.services.authorization import Authorization
from backend.app.security.limits import LoginLimits
from backend.app.security.errors import AuthError
from backend.app.contracts.auth import Credentials
from backend.app.persistence.transaction import unit_of_work
from backend.app.persistence.schema.identity import users,user_sessions,projects,project_members
from backend.app.persistence.schema.ocr import audit_events,schema_definitions

pytestmark=pytest.mark.db_integration


def service(auth_database):
    db,owner,manager,settings,_=auth_database
    return AuthService(db.engine,settings,manager.passwords,LoginLimits(settings.csrf_secret))


def subject(auth_database,user):
    auth=service(auth_database)
    _,raw=auth.login(Credentials(email=user['email'],password=PASSWORD),None,ip='synthetic',developer_allowed=False,request_id=str(uuid4()))
    return auth.current(raw),raw


@pytest.fixture
def project(auth_database):
    db,owner,manager,settings,_=auth_database
    user=email_user(auth_database);uid=UUID(user['id']);pid=uuid4()
    with unit_of_work(db.engine) as connection:
        connection.execute(select(users.c.user_id).where(users.c.user_id==uid).with_for_update(read=True)).all()
        connection.execute(projects.insert().values(project_id=pid,created_by=uid,name='합성 권한 프로젝트'))
        connection.execute(project_members.insert().values(project_id=pid,user_id=uid,role='owner'))
    return pid,user


@pytest.mark.parametrize('role,action,status',[
 ('viewer','read',200),('viewer','write',403),('viewer','manage',403),
 ('editor','read',200),('editor','write',200),('editor','manage',403),
 ('owner','read',200),('owner','write',200),('owner','manage',200)])
def test_project_role_matrix(auth_database,project,role,action,status):
    db,owner,manager,settings,_=auth_database;pid,_=project
    user=email_user(auth_database);uid=UUID(user['id']);manager.set_project_member(pid,uid,role,True)
    principal,_=subject(auth_database,user);authorization=Authorization(settings)
    with unit_of_work(db.engine) as connection:
        if status==200:assert authorization.project(connection,principal,pid,action=action)['project_id']==pid
        else:
            with pytest.raises(AuthError) as error:authorization.project(connection,principal,pid,action=action)
            assert error.value.status==status


@pytest.mark.parametrize('state',('outsider','inactive_member','revoked_session','expired_session'))
def test_access_rechecks_membership_and_session(auth_database,project,state):
    db,owner,manager,settings,_=auth_database;pid,_=project
    user=email_user(auth_database);uid=UUID(user['id'])
    if state!='outsider':manager.set_project_member(pid,uid,'editor',True)
    principal,raw=subject(auth_database,user)
    if state=='inactive_member':manager.set_project_member(pid,uid,'editor',False)
    elif state=='revoked_session':service(auth_database).logout(raw,request_id='synthetic')
    elif state=='expired_session':
        with owner.begin() as connection:
            connection.exec_driver_sql("UPDATE public.user_sessions SET created_at=clock_timestamp()-interval '9 hours',expires_at=clock_timestamp()-interval '1 hour' WHERE session_id=%s",(principal.session_id,))
    with unit_of_work(db.engine) as connection:
        with pytest.raises(AuthError) as error:Authorization(settings).project(connection,principal,pid,action='write')
        assert error.value.status==(404 if state in ('outsider','inactive_member') else 401)


def test_archival_allows_history_and_owner_restore_but_rejects_new_write(auth_database,project):
    db,_,_,settings,_=auth_database;pid,user=project;principal,_=subject(auth_database,user);authorization=Authorization(settings)
    with unit_of_work(db.engine) as connection:
        authorization.project(connection,principal,pid,action='manage')
        connection.execute(update(projects).where(projects.c.project_id==pid).values(business_state='archived',archived_at=func.clock_timestamp()))
    with unit_of_work(db.engine) as connection:
        assert authorization.project(connection,principal,pid)['business_state']=='archived'
        with pytest.raises(AuthError) as error:authorization.project(connection,principal,pid,action='write')
        assert error.value.code=='PROJECT_ARCHIVED'
    with unit_of_work(db.engine) as connection:
        authorization.project(connection,principal,pid,action='restore')
        connection.execute(update(projects).where(projects.c.project_id==pid).values(business_state='active',archived_at=None))
    with unit_of_work(db.engine) as connection:assert authorization.project(connection,principal,pid,action='write')['business_state']=='active'


@pytest.mark.parametrize('operation',('remove','disable'))
def test_last_owner_rejection_rolls_back_membership_user_sessions_and_audit(auth_database,project,operation):
    db,owner,manager,settings,_=auth_database;pid,user=project;uid=UUID(user['id']);principal,raw=subject(auth_database,user)
    with owner.connect() as connection:before=connection.execute(select(func.count()).select_from(audit_events).where(audit_events.c.project_id==pid)).scalar_one()
    with pytest.raises(AuthError) as error:
        if operation=='remove':manager.set_project_member(pid,uid,'viewer',False)
        else:manager.set_user_active(uid,False)
    assert error.value.code=='LAST_OWNER_REQUIRED'
    assert service(auth_database).current(raw) is not None
    with owner.connect() as connection:
        assert connection.execute(select(users.c.is_active).where(users.c.user_id==uid)).scalar_one()
        assert connection.execute(select(project_members.c.role).where(project_members.c.project_id==pid,project_members.c.user_id==uid)).scalar_one()=='owner'
        assert connection.execute(select(func.count()).select_from(audit_events).where(audit_events.c.project_id==pid)).scalar_one()==before


def test_two_owner_disables_have_one_winner_and_loser_keeps_session(auth_database,project):
    db,owner,manager,settings,_=auth_database;pid,user=project
    second=email_user(auth_database);manager.set_project_member(pid,UUID(second['id']),'owner',True)
    sessions={u['id']:subject(auth_database,u)[1] for u in (user,second)}
    barrier=threading.Barrier(2)
    def disable(u):
        barrier.wait(timeout=5)
        try:manager.set_user_active(UUID(u['id']),False);return u['id'],'disabled'
        except AuthError as error:return u['id'],error.code
    with ThreadPoolExecutor(max_workers=2) as workers:results=list(workers.map(disable,(user,second)))
    assert sorted(result for _,result in results)==['LAST_OWNER_REQUIRED','disabled']
    for uid,result in results:assert (service(auth_database).current(sessions[uid]) is not None)==(result=='LAST_OWNER_REQUIRED')


def test_global_metadata_only_schema_denied_and_file_access_uses_real_project(auth_database,project):
    from backend.tests.guard_fixtures import create_guard_graph
    from backend.app.persistence.schema.files import stored_files
    db,owner,manager,settings,_=auth_database;pid,user=project;principal,_=subject(auth_database,user)
    with db.engine.begin() as connection:
        schema=uuid4()
        # App role cannot register a global schema; owner does it explicitly below.
    with owner.begin() as connection:
        connection.execute(schema_definitions.insert().values(schema_id=schema,schema_name='synthetic-'+uuid4().hex,schema_version='v1'))
        foreign_user=email_user(auth_database)
        # A separate valid owner/member project, not a guessed URL project.
        foreign_id=uuid4()
        connection.execute(projects.insert().values(project_id=foreign_id,created_by=UUID(foreign_user['id']),name='foreign synthetic'))
        connection.execute(project_members.insert().values(project_id=foreign_id,user_id=UUID(foreign_user['id']),role='owner'))
        file=uuid4()
        connection.execute(stored_files.insert().values(file_id=file,project_id=foreign_id,file_role='schema_specification',storage_key='synthetic/'+uuid4().hex,byte_size=1,sha256='a'*64,created_by=UUID(foreign_user['id'])))
    authorization=Authorization(settings)
    with unit_of_work(db.engine) as connection:
        with pytest.raises(AuthError) as error:authorization.resource(connection,None,'stored_files',uuid4())
        assert error.value.status==401
        with pytest.raises(AuthError) as error:authorization.schema_for_validation(connection,principal,pid,schema)
        assert error.value.status==404
        with pytest.raises(AuthError) as error:authorization.resource(connection,principal,'stored_files',file,expected_project=pid)
        assert error.value.status==404
        with pytest.raises(AuthError) as error:authorization.resource(connection,principal,'stored_files',file)
        assert error.value.status==404


def test_database_outage_is_503_and_logout_retains_browser_cookies(auth_database,client,monkeypatch):
    db,owner,manager,settings,_=auth_database;user=email_user(auth_database)
    assert login(client,email=user['email']).status_code==200
    token=client.cookies[settings.session_cookie];headers=csrf(client)
    monkeypatch.setattr(db.engine,'connect',lambda:(_ for _ in ()).throw(RuntimeError('secret driver detail')))
    assert client.get('/api/v1/auth/me').status_code==503
    response=client.post('/api/v1/auth/logout',headers=headers)
    assert response.status_code==503 and 'set-cookie' not in response.headers
    assert client.cookies[settings.session_cookie]==token and 'secret driver detail' not in response.text
    client.cookies.clear()
    assert client.get('/api/v1/auth/me').status_code==503
    health=client.get('/api/v1/health');assert health.status_code==200 and health.json()['checks']['db']['state']=='not_ready'
