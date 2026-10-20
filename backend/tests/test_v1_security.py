"""Strict credentials, signed CSRF, fixed profiles and bounded real Argon2 operations."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json,subprocess,sys,time
from pathlib import Path
from uuid import uuid4
import pytest
pytest.importorskip('argon2');pytest.importorskip('email_validator')
from backend.app.config import ConfigurationError,Secret
from backend.app.v1_config import load_v1_settings,origin
from backend.app.security.passwords import Passwords,normalize_email,validate_password
from backend.app.security.csrf import Csrf,developer_allowed
from backend.app.security.limits import LoginLimits
from backend.app.security.errors import AuthError
from backend.app.contracts.auth import Credentials
from backend.tests.auth_fixtures import auth_environment,PASSWORD
from starlette.requests import Request
from pydantic import ValidationError


@pytest.mark.parametrize('patch',[
 {'PERSISTENCE_MODE':'legacy_json'},{'APP_ENV':'unknown'},{'APP_ENV':'production'},
 {'LOCAL_ADMIN_LOGIN':'true'},{'DEV_PROJECT_ID':''},{'DEV_PROJECT_ID':'no-uuid'},
 {'CSRF_SECRET':'short'},{'CURSOR_SECRET':'short'},
 {'CURSOR_SECRET':auth_environment()['CSRF_SECRET']},{'AUTH_ALLOWED_ORIGINS':''},
 {'AUTH_ALLOWED_ORIGINS':'http://*'},{'AUTH_ALLOWED_ORIGINS':'http://remote.example.com'},
 {'AUTH_ALLOWED_ORIGINS':'http://localhost:8000,http://localhost:8000'},
 {'AUTH_ALLOWED_HOSTS':'*'},{'AUTH_ALLOWED_HOSTS':'other.example.com'},
 {'AUTH_SESSION_SECONDS':'3600'},{'AUTH_CSRF_SECONDS':'100'},{'AUTH_MAX_CONCURRENT_HASHES':'3'},
 {'WEB_CONCURRENCY':'2'},{'UVICORN_WORKERS':'2'},
 {'DB_OWNER_PASSWORD':'must-not-be-exposed'},{'POSTGRES_PASSWORD':'must-not-be-exposed'}])
def test_invalid_v1_profiles_fail_without_revealing_secret(patch):
    with pytest.raises(ConfigurationError) as error:load_v1_settings(auth_environment(**patch))
    assert 'must-not-be-exposed' not in str(error.value)


def test_default_production_cookies_and_explicit_https():
    values=auth_environment(APP_ENV='production',LOCAL_ADMIN_LOGIN='0',AUTH_ALLOWED_ORIGINS='https://checker.example.com',AUTH_ALLOWED_HOSTS='checker.example.com')
    settings=load_v1_settings(values)
    assert settings.secure and settings.session_cookie=='__Host-baro_session'
    assert settings.csrf_cookie=='__Host-csrftoken' and settings.seed_cookie=='__Host-baro_csrf_seed'
    assert settings.csrf_secret.reveal() not in repr(settings)
    assert not hasattr(settings,'owner_password')


@pytest.mark.parametrize('email,expected',[
 ('  USER@Example.COM  ','user@example.com'),('user+tag@example.com','user+tag@example.com'),
 ('not-an-email',None),('사용자@example.com',None),('user@도메인.com',None),('user@localhost',None),('a'*255+'@example.com',None)])
def test_ascii_email_without_dns(email,expected,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:(_ for _ in ()).throw(AssertionError('DNS must not be used')))
    if expected:assert normalize_email(email)==expected
    else:
        with pytest.raises(AuthError):normalize_email(email)


@pytest.mark.parametrize('body',[
 {'email':'user@example.com','username':'admin','password':PASSWORD},
 {'email':None,'username':'admin','password':PASSWORD},
 {'username':None,'password':PASSWORD},{'password':PASSWORD},
 {'email':'user@example.com','password':True},{'email':'user@example.com','password':PASSWORD,'extra':'field'},
 {'username':'admin','password':''},{'email':'user@example.com','password':'a'*129},
 {'email':123,'password':PASSWORD}])
def test_invalid_credentials_have_no_fallback_and_hide_input(body):
    with pytest.raises(ValidationError) as error:Credentials.model_validate(body)
    assert PASSWORD not in str(error.value)


def test_password_is_preserved_and_real_argon2_costs_no_downgrade():
    from argon2 import PasswordHasher,extract_parameters,Type
    passwords=Passwords();value='  한글 비밀번호 123456  '
    encoded=passwords.hash(value);parameters=extract_parameters(encoded)
    assert (parameters.type,parameters.memory_cost,parameters.time_cost,parameters.parallelism)==(Type.ID,19456,2,1)
    assert passwords.verify(value,encoded)==(True,None)
    assert passwords.verify(value.strip(),encoded)==(False,None)
    assert passwords.verify(value,None)==(False,None)
    weak=PasswordHasher(memory_cost=8192,time_cost=1,parallelism=1).hash(value)
    good,upgraded=passwords.verify(value,weak);assert good and upgraded and extract_parameters(upgraded).memory_cost==19456
    strong=PasswordHasher(memory_cost=24576,time_cost=3,parallelism=1).hash(value)
    assert passwords.verify(value,strong)==(True,None)
    assert validate_password('admin')=='admin'
    with pytest.raises(AuthError):validate_password('admin',issuing=True)


def test_password_semaphore_rejects_without_waiting():
    passwords=Passwords()
    with passwords.slot(),passwords.slot():
        with pytest.raises(AuthError) as error:passwords.hash(PASSWORD)
        assert error.value.status==429
    assert passwords.verify(PASSWORD,passwords.hash(PASSWORD))[0]


def test_csrf_signature_binding_expiry_future_and_rotation():
    now=[1000.];csrf=Csrf(Secret('x'*32),clock=lambda:now[0])
    token=csrf.issue('session','private-session')
    assert csrf.valid(token,'session','private-session')
    assert 'private-session' not in token and len(token)<=256
    for purpose,binding in [('pre-auth','private-session'),('session','other')]:assert not csrf.valid(token,purpose,binding)
    assert not csrf.valid(token[:-1]+'a','session','private-session')
    now[0]=1900;assert not csrf.valid(token,'session','private-session')
    now[0]=969;assert not csrf.valid(token,'session','private-session')
    assert not csrf.valid('x'*257,'session','private-session')


def request(*,peer='127.0.0.1',host='localhost:8000',extra=()):
    return Request({'type':'http','method':'GET','scheme':'http','path':'/','root_path':'','query_string':b'',
        'server':('localhost',8000),'client':(peer,1234),
        'headers':[(b'host',host.encode()),(b'origin',b'http://localhost:8000'),*extra]})


@pytest.mark.parametrize('peer,host,headers',[
 ('203.0.113.1','localhost:8000',()),('127.0.0.1','remote.example.com',()),
 ('127.0.0.1','localhost:8000',((b'x-forwarded-for',b'127.0.0.1'),)),
 ('127.0.0.1','localhost:8000',((b'forwarded',b'for=127.0.0.1'),)),
 ('127.0.0.1','localhost:8000',((b'x-forwarded-proto',b'https'),))])
def test_developer_boundary_rejects_remote_and_forwarding(peer,host,headers):
    assert not developer_allowed(request(peer=peer,host=host,extra=headers),load_v1_settings(auth_environment()))


def test_limits_identity_ip_cooldown_capacity_and_safe_keys():
    now=[1000.];limits=LoginLimits(Secret('x'*32),clock=lambda:now[0])
    for _ in range(5):key=limits.before('127.0.0.1','email','private@example.com');limits.finished(key,False)
    assert 'private@example.com' not in repr(limits.entries)
    with pytest.raises(AuthError) as error:limits.before('other-ip','email','private@example.com')
    assert error.value.retry_after==900
    now[0]+=901;limits.before('127.0.0.1','email','private@example.com')
    limits=LoginLimits(Secret('x'*32),clock=lambda:now[0])
    for i in range(30):limits.before('same-ip','email',str(i))
    with pytest.raises(AuthError):limits.before('same-ip','email','new')
    small=LoginLimits(Secret('x'*32),capacity=2)
    small.before('ip','email','one')
    with pytest.raises(AuthError):small.before('different','email','two')
    assert len(small.entries)==2


def test_import_v1_does_not_open_files_db_network_models_or_start_legacy():
    code='''
from pathlib import Path
import socket,sqlalchemy,sys
import backend.app.config
import fastapi,pydantic

def denied(*a,**k):raise AssertionError('Unexpected import I/O')
Path.read_text=denied
socket.create_connection=denied
sqlalchemy.create_engine=denied
import backend.app.main,backend.app.management.accounts
assert 'backend.app.api' not in sys.modules
assert 'backend.app.ocr_runtime' not in sys.modules
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_json_security_formatter_uses_only_allowlisted_fields():
    import logging
    from backend.app.security.logging import SecurityFormatter
    record=logging.makeLogRecord({'msg':'sensitive password in an untrusted message','event':'auth_login_rejected',
        'request_id':'synthetic','reason':'INVALID_CREDENTIALS','password':'must-never-appear','token':'secret-token'})
    payload=json.loads(SecurityFormatter().format(record))
    assert payload=={'level':'Level None','event':'auth_login_rejected','request_id':'synthetic','reason':'INVALID_CREDENTIALS'}
    assert 'must-never-appear' not in str(payload) and 'secret-token' not in str(payload)
