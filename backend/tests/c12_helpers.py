"""C12 owned processes and explicit browser input. No credential output or app DB fallback."""
from contextlib import contextmanager
from pathlib import Path
import json,os,socket,subprocess,sys,time
from uuid import uuid4
import httpx
from backend.tests.auth_fixtures import auth_environment

ROOT=Path(__file__).resolve().parents[2]


def clean_environment():
    return {k:v for k,v in os.environ.items() if not k.startswith(('TEST_DB_','DB_','POSTGRES_','AUTH_')) and k not in ('CC_APP_ENV_FILE','CC_MIGRATION_ENV_FILE','APP_ENV','LOCAL_ADMIN_LOGIN','DEV_PROJECT_ID','CSRF_SECRET','CURSOR_SECRET','STORAGE_ROOT','REVIEW_PIPELINE','REVIEW_EXECUTOR','REVIEW_CONFIG_FILE','FRONTEND_DIST','RUN_TARGET','TRUSTED_PROXY_IPS')}


def wait_ready(url,process,timeout=20,*,verify=True):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        assert process is None or process.poll() is None,'Owned test service exited; inspect its private test log.'
        try:
            if httpx.get(url+'/api/v1/health',timeout=.5,trust_env=False,verify=verify).status_code==200:return
        except httpx.HTTPError:pass
        time.sleep(.04)
    raise AssertionError('Owned C12 service readiness timeout')


@contextmanager
def process(command,log,*,env=None,pass_fds=(),cwd=ROOT):
    child=subprocess.Popen(command,cwd=cwd,env=env or clean_environment(),pass_fds=pass_fds,stdout=log,stderr=log)
    try:yield child
    finally:
        if child.poll() is None:child.terminate()
        try:child.wait(timeout=8)
        except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)


@contextmanager
def server(db,root,log,*,pipeline='synthetic',extra_origins=(),executor='background_tasks',worker_config=None):
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);url='http://127.0.0.1:'+str(sock.getsockname()[1])
    values=auth_environment(db,AUTH_ALLOWED_ORIGINS=','.join((url,*extra_origins)),AUTH_ALLOWED_HOSTS='127.0.0.1,localhost',STORAGE_ROOT=str(root),FRONTEND_DIST=str(ROOT/'frontend/dist-v1'))
    if executor=='postgres_worker':
        assert pipeline=='synthetic' or pipeline=='disabled' and worker_config is not None
        values.update(REVIEW_EXECUTOR=executor,REVIEW_CONFIG_FILE=str(worker_config or root.parent/'synthetic-config-not-loaded.json'))
    else:assert executor=='background_tasks'
    factory='backend.app.main:create_app'
    env=clean_environment()
    if pipeline=='synthetic':
        gate=root.parent/('gate-'+uuid4().hex);gate.mkdir();(gate/'allow').touch();env['CC_C10_TEST_GATE_ROOT']=str(gate);factory='backend.tests.c10_live_factory:factory'
    elif pipeline=='paddle-only':values.update(REVIEW_PIPELINE='paddle-only',RUN_TARGET='local')
    else:assert pipeline=='disabled'
    appfile=root.parent/('app-'+uuid4().hex+'.env');appfile.write_text('\n'.join(k+'='+v for k,v in values.items())+'\n');appfile.chmod(0o600);env['CC_APP_ENV_FILE']=str(appfile)
    try:
        with process([sys.executable,'-m','uvicorn',factory,'--factory','--fd',str(sock.fileno()),'--workers','1','--no-proxy-headers','--no-access-log','--log-level','warning'],log,env=env,pass_fds=(sock.fileno(),)) as child:
            wait_ready(url,child);yield url,values
    finally:sock.close();appfile.unlink()


def browser_flow(config,timeout=90):
    env=clean_environment()
    completed=subprocess.run(['node','frontend/tests/live/v1-flow.mjs'],input=json.dumps(config),text=True,capture_output=True,env=env,cwd=ROOT,timeout=timeout)
    assert completed.returncode==0,'C12 browser failed: '+completed.stderr[-3500:]
    return json.loads(completed.stdout)


def login(client,email,password):
    token=client.get('/api/v1/auth/csrf').json()['csrf_token']
    response=client.post('/api/v1/auth/login',json={'email':email,'password':password},headers={'X-CSRFToken':token});assert response.status_code==200


def artifact(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
