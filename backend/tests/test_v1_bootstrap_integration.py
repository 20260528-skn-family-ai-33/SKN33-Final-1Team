"""Missing/disabled setup, real first-bootstrap race and production HTTPS ASGI profile."""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import threading
import pytest
from sqlalchemy import select,delete,func
from fastapi.testclient import TestClient
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_v1_auth_integration import auth_database,email_user,client,csrf,login  # noqa: F401
from backend.tests.auth_fixtures import auth_environment,DEV_PROJECT,PASSWORD
from backend.app.v1_config import load_v1_settings
from backend.app.main import create_app
from backend.app.security.errors import AuthError
from backend.app.persistence.schema.identity import users,projects,project_members

pytestmark=pytest.mark.db_integration


@pytest.mark.parametrize('auth_database',[False],indirect=True)
def test_missing_setup_first_bootstrap_race_and_no_implicit_reactivation(auth_database,monkeypatch):
    from backend.app.persistence.repositories import auth as repository
    db,owner,manager,settings,_=auth_database
    with TestClient(create_app(settings=settings,engine=db.engine),base_url='http://localhost:8000',client=('127.0.0.1',1234)) as browser:
        browser.headers['Origin']='http://localhost:8000'
        response=login(browser,username='admin',password='admin')
        assert response.status_code==503 and response.json()['error']['code']=='AUTH_SETUP_REQUIRED'
    barrier=threading.Barrier(2);original=repository.developer
    def synchronized(connection):
        row=original(connection)
        if row is None:barrier.wait(timeout=5)
        return row
    with monkeypatch.context() as patch:
        patch.setattr(repository,'developer',synchronized)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results=list(workers.map(lambda _:manager.bootstrap_development(DEV_PROJECT,environment='test',enabled=True),range(2)))
    assert results[0]==results[1]
    with owner.connect() as connection:
        assert connection.execute(select(func.count()).select_from(users).where(users.c.account_kind=='developer')).scalar_one()==1
        assert connection.execute(select(func.count()).select_from(projects)).scalar_one()==1
    replacement=email_user(auth_database)
    manager.set_project_member(DEV_PROJECT,UUID(replacement['id']),'owner',True)
    uid=UUID(results[0]['user']['id']);manager.set_user_active(uid,False)
    with pytest.raises(AuthError) as error:manager.bootstrap_development(DEV_PROJECT,environment='test',enabled=True)
    assert error.value.code=='AUTH_SETUP_REQUIRED'
    with TestClient(create_app(settings=settings,engine=db.engine),base_url='http://localhost:8000',client=('127.0.0.1',1234)) as browser:
        browser.headers['Origin']='http://localhost:8000'
        response=login(browser,username='admin',password='admin');assert response.status_code==503


