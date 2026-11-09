"""Signup against a disposable M008 database, using its restricted app role."""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from tests.app.test_db_integration import database  # noqa: F401
from tests.app.test_v1_auth_integration import auth_database,client,csrf,login  # noqa: F401
from tests.app.auth_fixtures import PASSWORD
from backend.app.main import create_app
from backend.app.persistence.schema.identity import users,project_members,user_sessions

pytestmark=pytest.mark.db_integration


def body(**changes):
    return {'email':uuid4().hex+'@example.com','display_name':'  합성 가입자  ','password':PASSWORD,**changes}


def signup(client,payload):
    return client.post('/api/v1/auth/signup',json=payload,headers=csrf(client))


def test_signup_persists_argon2_user_without_session_or_membership(auth_database,client):
    payload=body();response=signup(client,payload)
    assert response.status_code==201,response.text
    user=response.json()['user'];uid=UUID(user['id'])
    assert user['email']==payload['email'] and user['display_name']=='합성 가입자'
    assert 'password' not in response.text and 'token' not in response.text
    assert not client.get('/api/v1/auth/me').json()['authenticated']
    _,owner,manager,_,_=auth_database
    with owner.connect() as c:
        row=c.execute(select(users).where(users.c.user_id==uid)).mappings().one()
        assert row['is_active'] and row['account_kind']=='email'
        assert row['password_hash'].startswith('$argon2id$')
        assert manager.passwords.verify(PASSWORD,row['password_hash'])[0]
        assert c.execute(select(func.count()).select_from(project_members).where(project_members.c.user_id==uid)).scalar_one()==0
        assert c.execute(select(func.count()).select_from(user_sessions).where(user_sessions.c.user_id==uid)).scalar_one()==0
    assert login(client,email=payload['email']).status_code==200
    assert client.get('/api/v1/auth/me').json()['user']['id']==str(uid)


def test_duplicate_normalized_email_keeps_original_account(auth_database,client):
    payload=body();first=signup(client,payload)
    assert first.status_code==201
    second=signup(client,{**payload,'email':' '+payload['email'].upper()+' ','password':'another-synthetic-password-1'})
    assert second.status_code==409 and second.json()['error']['code']=='EMAIL_ALREADY_EXISTS'
    assert login(client,email=payload['email']).status_code==200
    assert client.get('/api/v1/auth/me').json()['user']['id']==first.json()['user']['id']


@pytest.mark.parametrize('changes',[{'password':'short'},{'role':'owner'},{'account_kind':'developer'},{'display_name':' '}])
def test_invalid_signup_never_creates_account(auth_database,client,changes):
    payload=body(**changes)
    response=signup(client,payload)
    assert response.status_code==422
    assert payload['password'] not in response.text
    with auth_database[1].connect() as c:
        assert not c.execute(select(users.c.user_id).where(users.c.email==payload['email'])).first()


def test_signup_requires_csrf_and_allowed_origin(client):
    payload=body()
    assert client.post('/api/v1/auth/signup',json=payload).status_code==403
    headers={**csrf(client),'Origin':'http://untrusted.invalid'}
    assert client.post('/api/v1/auth/signup',json=payload,headers=headers).status_code==403


def test_concurrent_duplicate_signup_creates_one_user(auth_database):
    db,owner,_,settings,_=auth_database
    payload=body()
    def attempt(_):
        with TestClient(create_app(settings=settings,engine=db.engine),base_url='http://localhost:8000') as c:
            c.headers['Origin']='http://localhost:8000'
            return signup(c,payload).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(attempt,range(2)))
    assert sorted(statuses)==[201,409]
    with owner.connect() as c:
        assert c.execute(select(func.count()).select_from(users).where(users.c.email==payload['email'])).scalar_one()==1


def test_signup_duplicate_attempts_use_existing_rate_limits(client):
    payload=body();assert signup(client,payload).status_code==201
    for _ in range(5):assert signup(client,payload).status_code==409
    response=signup(client,payload)
    assert response.status_code==429 and int(response.headers['retry-after'])>0
