"""Real FastAPI auth + real restricted PostgreSQL app role. Synthetic accounts only."""
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID,uuid4
from concurrent.futures import ThreadPoolExecutor
import json,threading,time
import pytest
from sqlalchemy import select,update,func
from fastapi.testclient import TestClient
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.auth_fixtures import auth_environment,DEV_PROJECT,PASSWORD
from backend.app.config import ConfigurationError
from backend.app.v1_config import load_v1_settings
from backend.app.main import create_app
from backend.app.management.accounts import AccountManager
from backend.app.security.passwords import Passwords
from backend.app.security.limits import LoginLimits
from backend.app.security.errors import AuthError
from backend.app.services.auth import AuthService,token_hash
from backend.app.services.authorization import Authorization
from backend.app.contracts.auth import Credentials
from backend.app.persistence.schema.identity import users,user_sessions,projects,project_members
from backend.app.persistence.transaction import unit_of_work
from backend.app.persistence.schema.ocr import audit_events

pytestmark=pytest.mark.db_integration


@pytest.fixture(scope='module')
def auth_database(database,request):
    from alembic import command
    from alembic.config import Config
    from backend.migrations.configuration import create_migration_engine,load_migration_settings
    env=database.environment
    settings=load_migration_settings({'DB_HOST':env['TEST_DB_HOST'],'DB_PORT':env['TEST_DB_PORT'],'POSTGRES_DB':database.name,
        'DB_OWNER_USER':env['TEST_DB_OWNER_USER'],'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'],'DB_APP_USER':env['TEST_DB_APP_USER']})
    config=Config(str(Path('backend/migrations/alembic.ini').resolve()));config.attributes['migration_settings']=settings
    owner=create_migration_engine(settings)
    try:
        command.upgrade(config,'head')
        passwords=Passwords();manager=AccountManager(owner,passwords,operator='synthetic-test-operator')
        setup=(manager.bootstrap_development(DEV_PROJECT,environment='test',enabled=True) if getattr(request,'param',True) else None)
        yield database,owner,manager,load_v1_settings(auth_environment(database)),setup
    finally:owner.dispose()


@pytest.fixture
def client(auth_database):
    db,_,_,settings,_=auth_database
    with TestClient(create_app(settings=settings,engine=db.engine),base_url='http://localhost:8000',client=('127.0.0.1',1234)) as current:
        current.headers['Origin']='http://localhost:8000'
        yield current


def email_user(auth_database,password=PASSWORD):
    return auth_database[2].create_email_user(uuid4().hex+'@example.com','합성 사용자',password)


def csrf(client):
    response=client.get('/api/v1/auth/csrf');assert response.status_code==200,response.text
    return {'X-CSRFToken':response.json()['csrf_token']}


def login(client,*,email=None,password=PASSWORD,username=None):
    body={'email':email,'password':password} if email is not None else {'username':username or 'admin','password':password}
    return client.post('/api/v1/auth/login',json=body,headers=csrf(client))


def test_email_login_real_argon2_session_public_projection_and_rotation(auth_database,client):
    db,owner,manager,settings,_=auth_database;user=email_user(auth_database)
    response=login(client,email='  '+user['email'].upper()+'  ')
    assert response.status_code==200,response.text
    assert response.json()['user']==user
    assert response.headers['cache-control']=='no-store'
    raw=client.cookies[settings.session_cookie]
    assert raw not in response.text and 'password_hash' not in response.text
    with owner.connect() as connection:
        row=connection.execute(select(user_sessions).where(user_sessions.c.token_hash==token_hash(raw))).mappings().one()
        assert row['user_id']==UUID(user['id']) and (row['expires_at']-row['created_at']).total_seconds()==28800
        assert raw not in str(row)
    assert client.get('/api/v1/auth/me').json()['user']==user
    assert login(client,email=user['email']).status_code==200
    assert client.cookies[settings.session_cookie]!=raw
    with owner.connect() as connection:assert connection.execute(select(user_sessions.c.revoked_at).where(user_sessions.c.token_hash==token_hash(raw))).scalar_one() is not None


def test_session_survives_app_restart_and_distinct_browser_logout(auth_database,client):
    db,owner,_,settings,_=auth_database;user=email_user(auth_database)
    assert login(client,email=user['email']).status_code==200
    with TestClient(create_app(settings=settings,engine=db.engine),base_url='http://localhost:8000',client=('127.0.0.1',9999)) as second:
        second.headers['Origin']='http://localhost:8000'
        second.cookies.update(client.cookies)
        assert second.get('/api/v1/auth/me').json()['user']==user
        second.cookies.clear();assert login(second,email=user['email']).status_code==200
        assert client.post('/api/v1/auth/logout',headers=csrf(client)).status_code==204
        assert second.get('/api/v1/auth/me').json()['authenticated']
        assert client.post('/api/v1/auth/logout',headers=csrf(client)).status_code==204
        assert not client.get('/api/v1/auth/me').json()['authenticated']


@pytest.mark.parametrize('kind',('missing','wrong_password','disabled'))
def test_invalid_credentials_are_indistinguishable_and_create_no_session(auth_database,client,kind):
    db,owner,manager,settings,_=auth_database;user=email_user(auth_database)
    if kind=='disabled':manager.set_user_active(UUID(user['id']),False)
    with owner.connect() as connection:before=connection.execute(select(func.count()).select_from(user_sessions)).scalar_one()
    response=login(client,email=uuid4().hex+'@example.com' if kind=='missing' else user['email'],password='wrong-password' if kind=='wrong_password' else PASSWORD)
    assert response.status_code==401 and response.json()['error']['code']=='INVALID_CREDENTIALS'
    assert response.json()['error']['message']=='이메일 또는 비밀번호를 확인해 주세요.'
    assert not client.cookies.get(settings.session_cookie)
    with owner.connect() as connection:assert connection.execute(select(func.count()).select_from(user_sessions)).scalar_one()==before


@pytest.mark.parametrize('body',[
 {'email':'user@example.com','username':'admin','password':'admin'},
 {'email':None,'username':'admin','password':'admin'},
 {'email':'user@example.com','password':True},{'username':'admin','password':'admin','extra':True},
 {'email':'user@example.com','password':'a'*129},{'password':'admin'},{'username':None,'password':'admin'}])
def test_http_invalid_auth_structure_is_422_and_never_echoes_secret(client,body):
    response=client.post('/api/v1/auth/login',json=body,headers=csrf(client))
    assert response.status_code==422 and response.json()['error']['code']=='INVALID_AUTH_REQUEST'
    assert 'admin' not in response.text and 'user@example.com' not in response.text


@pytest.mark.parametrize('content,content_type,status',[
 pytest.param('{"username":"admin","username":"admin","password":"admin"}','application/json',400,id='duplicate-key'),
 pytest.param('{"password":NaN}','application/json',400,id='nonfinite-number'),
 pytest.param('{','application/json',400,id='invalid-json'),
 pytest.param('x'*4097,'application/json',413,id='oversized-body'),
 pytest.param('{}','text/plain',415,id='wrong-content-type'),
])
def test_http_body_parser_is_bounded_strict_and_safe(client,content,content_type,status):
    response=client.post('/api/v1/auth/login',content=content,headers={**csrf(client),'Content-Type':content_type})
    assert response.status_code==status


def test_developer_login_is_real_uuid_and_old_cookie_not_trusted(auth_database,client):
    db,owner,_,settings,setup=auth_database
    assert client.get('/api/v1/auth/csrf').json()['developer_login_available']
    response=login(client,username='admin',password='admin');assert response.status_code==200,response.text
    assert response.json()['user']==setup['user']
    assert response.json()['user']['id']!='local-admin'
    raw=client.cookies[settings.session_cookie];client.cookies.clear()
    client.cookies.set('baro_local_session',raw)
    assert not client.get('/api/v1/auth/me').json()['authenticated']


@pytest.mark.parametrize('headers',[
 {'X-Forwarded-For':'127.0.0.1'},{'Forwarded':'for=127.0.0.1'},{'X-Forwarded-Proto':'https'}])
def test_forwarded_developer_login_and_existing_developer_session_rejected(auth_database,client,headers):
    assert login(client,username='admin',password='admin').status_code==200
    response=client.post('/api/v1/auth/login',json={'username':'admin','password':'admin'},headers={**csrf(client),**headers})
    assert response.status_code==404
    assert not client.get('/api/v1/auth/me',headers=headers).json()['authenticated']


def test_csrf_rotation_other_session_forgery_expiry_and_origin(auth_database,client):
    user=email_user(auth_database);old=csrf(client)['X-CSRFToken']
    assert login(client,email=user['email']).status_code==200
    for headers in ({'X-CSRFToken':old},{'X-CSRFToken':'forged'},{}):
        assert client.post('/api/v1/auth/logout',headers=headers).status_code==403
    valid=csrf(client)
    for source in ('https://evil.example.com','null','http://localhost:8000.evil.com'):
        assert client.post('/api/v1/auth/logout',headers={**valid,'Origin':source}).status_code==403
    del client.headers['Origin']
    assert client.post('/api/v1/auth/logout',headers=valid).status_code==403
    assert client.post('/api/v1/auth/logout',headers={**valid,'Referer':'http://localhost:8000/login'}).status_code==204


@pytest.mark.parametrize('action',('reset','email','disable'))
def test_account_changes_revoke_all_sessions_atomically(auth_database,client,action):
    db,owner,manager,settings,_=auth_database;user=email_user(auth_database);uid=UUID(user['id'])
    assert login(client,email=user['email']).status_code==200
    if action=='reset':manager.reset_password(uid,PASSWORD+'new')
    elif action=='email':manager.change_email(uid,uuid4().hex+'@example.com')
    else:manager.set_user_active(uid,False)
    assert not client.get('/api/v1/auth/me').json()['authenticated']
    with owner.connect() as connection:
        assert connection.execute(select(func.count()).select_from(user_sessions).where(user_sessions.c.user_id==uid,user_sessions.c.revoked_at.is_(None))).scalar_one()==0
    if action=='disable':
        manager.set_user_active(uid,True)
        assert not client.get('/api/v1/auth/me').json()['authenticated']


def test_email_normalization_duplicate_does_not_overwrite(auth_database):
    db,owner,manager,*_=auth_database;user=email_user(auth_database)
    with pytest.raises(AuthError) as error:manager.create_email_user(' '+user['email'].upper()+' ','other',PASSWORD)
    assert error.value.code=='EMAIL_ALREADY_EXISTS'
    with owner.connect() as connection:assert connection.execute(select(users.c.display_name).where(users.c.user_id==UUID(user['id']))).scalar_one()=='합성 사용자'


def test_bootstrap_repeat_conflict_inactive_and_concurrent_reuse(auth_database):
    db,owner,manager,settings,setup=auth_database
    assert manager.bootstrap_development(DEV_PROJECT,environment='test',enabled=True)==setup
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(lambda _:manager.bootstrap_development(DEV_PROJECT,environment='test',enabled=True),range(2)))
    assert results==[setup,setup]
    with pytest.raises(AuthError) as error:manager.bootstrap_development(uuid4(),environment='test',enabled=True)
    assert error.value.code=='DEVELOPMENT_CONFLICT'
    with owner.connect() as connection:assert connection.execute(select(func.count()).select_from(users).where(users.c.account_kind=='developer')).scalar_one()==1


def test_manager_refuses_app_role_and_web_has_no_account_admin_routes(auth_database,client):
    db,_,_,settings,_=auth_database
    with pytest.raises(AuthError) as error:AccountManager(db.engine,Passwords(),operator='test').create_email_user(uuid4().hex+'@example.com','forbidden',PASSWORD)
    assert error.value.code=='MANAGEMENT_FORBIDDEN'
    assert login(client,username='admin',password='admin').status_code==200
    assert client.post('/api/v1/management/create-email-user',json={},headers=csrf(client)).status_code==404


def test_production_active_developer_startup_refused(auth_database):
    db,_,_,_,_=auth_database
    settings=load_v1_settings(auth_environment(db,APP_ENV='production',LOCAL_ADMIN_LOGIN='0',AUTH_ALLOWED_ORIGINS='https://localhost',AUTH_ALLOWED_HOSTS='localhost'))
    with pytest.raises(ConfigurationError):
        with TestClient(create_app(settings=settings,engine=db.engine),base_url='https://localhost'):pass


def test_http_attempt_and_hash_concurrency_limits_are_429(client):
    email=uuid4().hex+'@example.com'
    for _ in range(5):assert login(client,email=email).status_code==401
    response=login(client,email=email)
    assert response.status_code==429 and int(response.headers['retry-after'])>0
    passwords=client.app.state.auth.passwords
    with passwords.slot(),passwords.slot():
        response=login(client,email=uuid4().hex+'@example.com')
        assert response.status_code==429 and response.headers['retry-after']=='1'
        assert client.get('/api/v1/auth/me').status_code==200


def test_http_csrf_rejects_other_session_token_and_expired_signature(auth_database,client,monkeypatch):
    from backend.app.security.csrf import Csrf
    user=email_user(auth_database);assert login(client,email=user['email']).status_code==200
    settings=auth_database[3];raw=client.cookies[settings.session_cookie];token=csrf(client)['X-CSRFToken']
    response=client.post('/api/v1/auth/logout',headers={'X-CSRFToken':token,'Cookie':settings.session_cookie+'='+'a'*43+'; '+settings.csrf_cookie+'='+token})
    assert response.status_code==403
    signer=client.app.state.csrf;now=signer.clock();monkeypatch.setattr(signer,'clock',lambda:now+901)
    response=client.post('/api/v1/auth/logout',headers={'X-CSRFToken':token})
    assert response.status_code==403 and client.cookies[settings.session_cookie]==raw


def test_expired_session_can_logout_and_first_revocation_time_is_preserved(auth_database,client):
    db,owner,manager,settings,_=auth_database;user=email_user(auth_database)
    assert login(client,email=user['email']).status_code==200;raw=client.cookies[settings.session_cookie]
    with owner.begin() as connection:
        connection.exec_driver_sql("UPDATE public.user_sessions SET created_at=clock_timestamp()-interval '9 hours',expires_at=clock_timestamp()-interval '1 hour' WHERE token_hash=%s",(token_hash(raw),))
    assert not client.get('/api/v1/auth/me').json()['authenticated']
    assert client.post('/api/v1/auth/logout',headers=csrf(client)).status_code==204
    with owner.connect() as connection:first=connection.execute(select(user_sessions.c.revoked_at).where(user_sessions.c.token_hash==token_hash(raw))).scalar_one()
    client.cookies.set(settings.session_cookie,raw,domain='localhost.local',path='/')
    assert client.post('/api/v1/auth/logout',headers=csrf(client)).status_code==204
    with owner.connect() as connection:assert connection.execute(select(user_sessions.c.revoked_at).where(user_sessions.c.token_hash==token_hash(raw))).scalar_one()==first


def test_real_login_rehash_happens_outside_connections_and_commits_safely(auth_database,client,monkeypatch):
    from argon2 import PasswordHasher,extract_parameters
    db,owner,manager,settings,_=auth_database;user=email_user(auth_database);uid=UUID(user['id'])
    weak=PasswordHasher(memory_cost=8192,time_cost=1,parallelism=1).hash(PASSWORD)
    with owner.begin() as connection:connection.execute(update(users).where(users.c.user_id==uid).values(password_hash=weak))
    original=client.app.state.auth.passwords.verify
    def verify(password,encoded):
        assert db.engine.pool.checkedout()==0
        return original(password,encoded)
    monkeypatch.setattr(client.app.state.auth.passwords,'verify',verify)
    assert login(client,email=user['email']).status_code==200
    with owner.connect() as connection:current=connection.execute(select(users.c.password_hash).where(users.c.user_id==uid)).scalar_one()
    assert current!=weak and extract_parameters(current).memory_cost==19456


def test_security_logs_and_public_objects_exclude_credentials_hashes_and_tokens(auth_database,client,caplog):
    import logging
    caplog.set_level(logging.INFO,logger='creative_checker.security')
    user=email_user(auth_database);assert login(client,email=user['email']).status_code==200
    raw=client.cookies[auth_database[3].session_cookie]
    assert login(client,email=user['email'],password='incorrect synthetic password').status_code==401
    assert client.post('/api/v1/auth/logout',headers=csrf(client)).status_code==204
    records=[r for r in caplog.records if r.name=='creative_checker.security']
    assert {'auth_login_success','auth_login_rejected','auth_logout_success'}.issubset({getattr(r,'event',None) for r in records})
    source=str([vars(record) for record in records])
    for private in (PASSWORD,raw,token_hash(raw),'incorrect synthetic password','$argon2id$',user['email']):assert private not in source
