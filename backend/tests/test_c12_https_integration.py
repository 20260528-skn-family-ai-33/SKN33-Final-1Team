"""Actual nginx TLS + non-root Linux API + PostgreSQL + browser. Local test cert only."""
from contextlib import contextmanager
from pathlib import Path
import json,os,subprocess,time,ssl
from uuid import uuid4
import httpx,pytest
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user
from backend.tests.auth_fixtures import auth_environment,PASSWORD
from backend.tests.c12_helpers import ROOT,browser_flow,artifact

pytestmark=pytest.mark.db_integration


def docker(*args,check=True):
    value=subprocess.run(['docker',*args],text=True,capture_output=True,timeout=90)
    assert not check or value.returncode==0,'Owned C12 Docker operation failed: '+value.stderr[-1500:]
    return value.stdout.strip()


@contextmanager
def stack(db,tmp):
    assert os.environ.get('C12_RUN_HTTPS')=='1','Select HTTPS tests only with C12_RUN_HTTPS=1 and a built C12_API_IMAGE.'
    image=os.environ.get('C12_API_IMAGE');assert image,'Explicit C12_API_IMAGE required'
    tag='cc-c12-'+uuid4().hex[:12];network=tag+'-net';volume=tag+'-data';proxy=tag+'-proxy';api=tag+'-api';echo=tag+'-echo'
    tmp=tmp.resolve();tls=tmp/'tls';tls.mkdir()
    made_network=made_volume=False
    try:
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost',
            '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1','-keyout',str(tls/'key.pem'),'-out',str(tls/'cert.pem')],capture_output=True,check=True)
        template=(ROOT/'deploy/nginx/v1.conf.template').read_text()
        for key,value in {'CC_SERVER_NAME':'localhost','CC_TLS_CERT':'/tls/cert.pem','CC_TLS_KEY':'/tls/key.pem','CC_UPSTREAM':'api:8000'}.items():template=template.replace('${'+key+'}',value)
        config=tmp/'nginx.conf';config.write_text(template)
        docker('network','create',network);made_network=True;docker('volume','create',volume);made_volume=True
        docker('run','-d','--name',proxy,'--network',network,'-p','127.0.0.1::443','--mount',f'type=bind,source={config},target=/etc/nginx/conf.d/default.conf,readonly','--mount',f'type=bind,source={tls},target=/tls,readonly','nginx:1.28-alpine')
        mapping=docker('port',proxy,'443/tcp');port=mapping.rsplit(':',1)[1];url='https://localhost:'+port
        proxy_ip=json.loads(docker('inspect',proxy))[0]['NetworkSettings']['Networks'][network]['IPAddress']
        # Exercise the actual proxy wire allowlist before attaching the real app.
        script="from http.server import BaseHTTPRequestHandler,HTTPServer\nimport json\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  data=json.dumps(dict(self.headers)).encode();self.send_response(200);self.end_headers();self.wfile.write(data)\n def log_message(self,*args):pass\nHTTPServer(('0.0.0.0',8000),H).serve_forever()"
        docker('run','-d','--name',echo,'--network',network,'--network-alias','api',image,'python','-c',script)
        deadline=time.monotonic()+25;wire=None
        while time.monotonic()<deadline:
            try:
                response=httpx.get(url+'/__wire_probe__',verify=ssl.create_default_context(cafile=str(tls/'cert.pem')),trust_env=False,headers={'Forwarded':'for=198.51.100.1;proto=http','X-Forwarded-For':'198.51.100.1','X-Forwarded-Proto':'http','X-Forwarded-Prefix':'/evil','X-Forwarded-Evil':'secret'},timeout=1)
                if response.status_code==200:wire={k.lower():v for k,v in response.json().items()};break
            except httpx.HTTPError:pass
            time.sleep(.1)
        assert wire is not None,'nginx wire probe did not start'
        assert 'forwarded' not in wire and 'x-forwarded-prefix' not in wire and 'x-forwarded-evil' not in wire
        assert wire['x-forwarded-proto']=='https' and '198.51.100.1' not in wire['x-forwarded-for']
        docker('rm','-f',echo)
        docker('run','--rm','--mount',f'type=volume,source={volume},target=/data',image,'python','-m','backend.app.storage','--root','/data/storage','init')
        values=auth_environment(db,APP_ENV='production',LOCAL_ADMIN_LOGIN='0',DEV_PROJECT_ID='',DB_HOST=os.environ.get('C12_CONTAINER_DB_HOST','host.docker.internal'),AUTH_ALLOWED_ORIGINS=url,AUTH_ALLOWED_HOSTS='localhost',STORAGE_ROOT='/data/storage',FRONTEND_DIST='/web',TRUSTED_PROXY_IPS=proxy_ip)
        envfile=tmp/'linux.env';envfile.write_text('\n'.join(k+'='+v for k,v in values.items())+'\n');envfile.chmod(0o600)
        docker('run','-d','--name',api,'--network',network,'--network-alias','api','--add-host','host.docker.internal:host-gateway','--read-only','--tmpfs','/tmp:rw,uid=10001,gid=10001,mode=0700','--env-file',str(envfile),'--mount',f'type=volume,source={volume},target=/data','--mount',f'type=bind,source={ROOT / "frontend/dist-v1"},target=/web,readonly',image)
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            assert json.loads(docker('inspect',api))[0]['State']['Running'],'Linux API exited: '+docker('logs',api)[-1500:]
            try:
                if httpx.get(url+'/api/v1/health',verify=ssl.create_default_context(cafile=str(tls/'cert.pem')),trust_env=False,timeout=1).status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.1)
        else:raise AssertionError('Linux HTTPS API readiness timeout')
        yield dict(url=url,cert=str(tls/'cert.pem'),wire=wire,api=api,proxy=proxy,image=image,proxy_ip=proxy_ip)
        logs=docker('logs',api)
        assert db.settings.database.password.reveal() not in logs and PASSWORD not in logs
    finally:
        for name in (api,echo,proxy):docker('rm','-f',name,check=False)
        if made_volume:docker('volume','rm',volume)
        if made_network:docker('network','rm',network)
        (tmp/'linux.env').unlink(missing_ok=True)


@pytest.mark.parametrize('auth_database',[False],indirect=True)
def test_linux_nginx_https_cookies_csrf_ui_upload_and_trust(auth_database,tmp_path):
    db=auth_database[0];user=email_user(auth_database)
    with stack(db,tmp_path) as target:
        result=browser_flow(dict(url=target['url'],https=True,email=user['email'],password=PASSWORD,image=str(ROOT/'frontend/tests/v1/poster.png'),disabled=True,screenshotDir=str(tmp_path/'screenshots')))
        assert result['secure_cookie_verified'] and result['disabled_runtime_rejected']
        with httpx.Client(base_url=target['url'],verify=ssl.create_default_context(cafile=target['cert']),trust_env=False) as client:
            for path in ('/login','/projects','/projects/'+result['project_id']+'/drafts/'+result['draft_id']):
                response=client.get(path);assert response.status_code==200 and response.headers['content-type'].startswith('text/html')
                assert response.headers['strict-transport-security']=='max-age=31536000' and response.headers['x-content-type-options']=='nosniff'
            for path in ('/api/v1/no-such-api','/assets/missing-12345678.js','/.env','/storage/private','/backend/app/main.py'):
                response=client.get(path);assert response.status_code==404 and 'text/html' not in response.headers['content-type']
            prepared=client.get('/api/v1/auth/csrf');assert not prepared.json()['developer_login_available']
            token=prepared.json()['csrf_token'];body={'email':user['email'],'password':PASSWORD}
            for origin in ('https://foreign.invalid','null'):
                assert client.post('/api/v1/auth/login',json=body,headers={'Origin':origin,'X-CSRFToken':token}).status_code==403
            assert client.post('/api/v1/auth/login',json=body,headers={'Origin':target['url']}).status_code==403
            # Forged transport headers are removed/replaced by nginx, so real HTTPS remains usable.
            assert client.post('/api/v1/auth/login',json=body,headers={'Origin':target['url'],'X-CSRFToken':token,'X-Forwarded-Proto':'http','Forwarded':'proto=http'}).status_code==200
        probe="import urllib.request,urllib.error;req=urllib.request.Request('http://127.0.0.1:8000/api/v1/health',headers={'Host':'localhost','X-Forwarded-Proto':'https'});\ntry:urllib.request.urlopen(req);raise AssertionError('untrusted proxy accepted')\nexcept urllib.error.HTTPError as e:assert e.code==400"
        docker('exec',target['api'],'python','-c',probe)
        assert docker('exec',target['api'],'id','-u')=='10001'
        artifact(tmp_path/'c12-https-result.json',dict(flow=result,linux=True,nonroot_uid=10001,proxy='nginx:1.28-alpine',proxy_wire_allowlist_verified=True,untrusted_forwarded_rejected=True,temporary_tls_certificate=True,actual_ocr_in_this_profile=False))
