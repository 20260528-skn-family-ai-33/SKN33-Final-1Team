"""Missing/disabled setup, real first-bootstrap race and production HTTPS ASGI profile."""
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from uuid import UUID,uuid4
import threading
import pytest
from sqlalchemy import select,delete,func
from fastapi.testclient import TestClient
from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database,email_user,client,csrf,login,use_legacy_session_cookie  # noqa: F401
from tests.app.auth_fixtures import auth_environment,DEV_PROJECT,PASSWORD
from backend.app.v1_config import load_v1_settings
from backend.app.main import create_app
from backend.app.security.errors import AuthError
from backend.app.persistence.schema.identity import users,projects,project_members,user_sessions
from backend.app.services.auth import token_hash

pytestmark=pytest.mark.db_integration


def test_production_https_cookie_attributes_and_developer_block(auth_database):
    db,owner,manager,_,setup=auth_database
    user=email_user(auth_database);uid=UUID(user['id']);dev=UUID(setup['user']['id'])
    manager.set_project_member(DEV_PROJECT,uid,'owner',True);manager.set_user_active(dev,False)
    settings=load_v1_settings(auth_environment(db,APP_ENV='production',LOCAL_ADMIN_LOGIN='0',AUTH_ALLOWED_ORIGINS='https://localhost',AUTH_ALLOWED_HOSTS='localhost'))
    try:
        with TestClient(create_app(settings=settings,engine=db.engine),base_url='https://localhost',client=('203.0.113.9',1234)) as browser:
            browser.headers['Origin']='https://localhost'
            prepared=browser.get('/api/v1/auth/csrf')
            assert not prepared.json()['developer_login_available']
            cookies=prepared.headers.get_list('set-cookie')
            assert all('Secure' in c and 'SameSite=strict' in c and 'Path=/' in c and 'Domain=' not in c for c in cookies)
            assert any('__Host-baro_csrf_seed=' in c and 'HttpOnly' in c for c in cookies)
            response=browser.post('/api/v1/auth/login',json={'email':user['email'],'password':PASSWORD},headers={'X-CSRFToken':prepared.json()['csrf_token']})
            assert response.status_code==200,response.text
            session=next(c for c in response.headers.get_list('set-cookie') if c.startswith('__Host-creative_checker_session='))
            assert 'HttpOnly' in session and 'Secure' in session and 'Max-Age=28800' in session and 'Domain=' not in session
            raw = use_legacy_session_cookie(browser, settings)
            sessions = select(user_sessions).where(user_sessions.c.user_id == uid)
            with owner.connect() as connection:
                previous = connection.execute(sessions).mappings().one()
            csrf_token = browser.cookies[settings.csrf_cookie]
            migrated = browser.get('/api/v1/auth/me')
            assert migrated.json()['user'] == user
            assert migrated.headers['cache-control'] == 'no-store'
            migrated_cookie = SimpleCookie(next(c for c in migrated.headers.get_list('set-cookie')
                if c.startswith('__Host-creative_checker_session=')))[settings.session_cookie]
            assert migrated_cookie['secure'] and migrated_cookie['httponly'] and migrated_cookie['samesite'] == 'strict'
            assert migrated_cookie['path'] == '/' and not migrated_cookie['domain'] and not migrated_cookie['max-age']
            assert parsedate_to_datetime(migrated_cookie['expires']) == previous['expires_at'].replace(microsecond=0)
            assert previous['token_hash'] == token_hash(raw) and browser.cookies[settings.session_cookie] == raw
            assert settings.legacy_session_cookie not in browser.cookies
            assert browser.cookies[settings.csrf_cookie] == csrf_token
            with owner.connect() as connection:
                assert connection.execute(sessions).mappings().one() == previous
            assert browser.get('/api/v1/auth/me').json()['user']==user
            assert browser.get('http://localhost/api/v1/auth/me').status_code==400
            assert login(browser,username='admin',password='admin').status_code==404
            health=browser.get('/api/v1/health').json()
            assert health['status']=='degraded' and health['checks']['worker']['state']=='unknown' and health['checks']['model']['checked_at'] is None
    finally:
        manager.set_user_active(dev,True)
        with owner.begin() as connection:connection.execute(delete(project_members).where(project_members.c.project_id==DEV_PROJECT,project_members.c.user_id==uid))


def test_remote_peer_and_disabled_flag_reject_existing_developer_cookie(auth_database):
    db,_,_,settings,_=auth_database
    with TestClient(create_app(settings=settings,engine=db.engine),base_url='http://localhost:8000',client=('127.0.0.1',1234)) as local:
        local.headers['Origin']='http://localhost:8000'
        assert login(local,username='admin',password='admin').status_code==200
        cookies=local.cookies
    for profile,peer in ((settings,'203.0.113.10'),(load_v1_settings(auth_environment(db,LOCAL_ADMIN_LOGIN='0')),'127.0.0.1')):
        with TestClient(create_app(settings=profile,engine=db.engine),base_url='http://localhost:8000',client=(peer,1234)) as browser:
            browser.headers['Origin']='http://localhost:8000';browser.cookies.update(cookies)
            assert not browser.get('/api/v1/auth/me').json()['authenticated']
            assert login(browser,username='admin',password='admin').status_code==404
