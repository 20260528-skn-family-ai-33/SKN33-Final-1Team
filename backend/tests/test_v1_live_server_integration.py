"""Run the documented factory in a real Uvicorn process with app-only environment."""
from contextlib import contextmanager
from pathlib import Path
import json,os,socket,subprocess,sys,time
import httpx,pytest
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_v1_auth_integration import auth_database,email_user  # noqa: F401
from backend.tests.auth_fixtures import auth_environment,PASSWORD

pytestmark=pytest.mark.db_integration


@contextmanager
def server(auth_database,log):
    db,*_=auth_database
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    address='http://127.0.0.1:'+str(sock.getsockname()[1])
    environment={k:v for k,v in os.environ.items() if not k.startswith('TEST_DB_') and k not in (
        'DB_OWNER_PASSWORD','DB_OWNER_USER','POSTGRES_PASSWORD','CC_MIGRATION_ENV_FILE','CC_APP_ENV_FILE')}
    values=auth_environment(db,AUTH_ALLOWED_ORIGINS=address,AUTH_ALLOWED_HOSTS='127.0.0.1')
    environment={k:v for k,v in environment.items() if k not in values}
    app_file=Path(str(log.name)+'.app.env')
    app_file.write_text('\n'.join(k+'='+v for k,v in values.items())+'\n');app_file.chmod(0o600)
    environment['CC_APP_ENV_FILE']=str(app_file)
    assert not any('OWNER_PASSWORD' in k or k=='POSTGRES_PASSWORD' for k in environment)
    process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:create_app','--factory',
        '--fd',str(sock.fileno()),'--workers','1','--no-proxy-headers','--no-access-log','--log-level','warning'],
        env=environment,pass_fds=(sock.fileno(),),stdout=log,stderr=log)
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if process.poll() is not None:raise AssertionError('Synthetic Uvicorn process exited before readiness; no driver output exposed.')
            try:
                with httpx.Client(trust_env=False,timeout=.5) as probe:
                    if probe.get(address+'/api/v1/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.05)
        else:raise AssertionError('Synthetic server did not become ready within the bound.')
        yield address
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
        sock.close()
        app_file.unlink()


def test_real_factory_environment_http_and_process_restart_keep_db_session(auth_database,tmp_path,request):
    user=email_user(auth_database);settings=auth_database[3]
    path=tmp_path/'server.log'
    with path.open('w+') as log:
        with server(auth_database,log) as address:
            with httpx.Client(base_url=address,headers={'Origin':address},trust_env=False,timeout=3) as browser:
                prepared=browser.get('/api/v1/auth/csrf');assert prepared.status_code==200
                response=browser.post('/api/v1/auth/login',json={'email':user['email'],'password':PASSWORD},headers={'X-CSRFToken':prepared.json()['csrf_token']})
                assert response.status_code==200 and response.json()['user']==user
                cookies=browser.cookies
                assert browser.get('/api/v1/auth/me').json()['user']==user
        with server(auth_database,log) as address:
            with httpx.Client(base_url=address,cookies=cookies,headers={'Origin':address},trust_env=False,timeout=3) as browser:
                assert browser.get('/api/v1/auth/me').json()['user']==user
                prepared=browser.get('/api/v1/auth/csrf')
                assert browser.post('/api/v1/auth/logout',headers={'X-CSRFToken':prepared.json()['csrf_token']}).status_code==204
                assert not browser.get('/api/v1/auth/me').json()['authenticated']
        log.flush();log.seek(0);source=log.read()
    for private in (PASSWORD,user['email'],settings.database.password.reveal()):assert private not in source
    request.node.user_properties.append(('v1_live_server',json.dumps({'factory_from_environment':True,'explicit_literal_app_file':True,'app_only_credentials':True,
        'real_loopback_http':True,'process_restarts':1,'session_survived':True,'logout_persisted':True,'secrets_absent_from_logs':True})))
