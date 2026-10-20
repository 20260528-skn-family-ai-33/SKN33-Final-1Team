"""Real owner CLI in a child process and PTY. Passwords never appear in argv/output."""
from pathlib import Path
from uuid import UUID,uuid4
import json,os,pty,select,subprocess,sys,time
import pytest
from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_v1_auth_integration import auth_database  # noqa: F401
from backend.tests.auth_fixtures import DEV_PROJECT,PASSWORD

pytestmark=pytest.mark.db_integration


@pytest.fixture
def owner_file(auth_database,tmp_path):
    db,*_=auth_database;env=db.environment
    values={'DB_HOST':env['TEST_DB_HOST'],'DB_PORT':env['TEST_DB_PORT'],'POSTGRES_DB':db.name,
        'DB_OWNER_USER':env['TEST_DB_OWNER_USER'],'DB_OWNER_PASSWORD':env['TEST_DB_OWNER_PASSWORD'],
        'DB_APP_USER':env['TEST_DB_APP_USER'],'APP_ENV':'test','LOCAL_ADMIN_LOGIN':'1','DEV_PROJECT_ID':str(DEV_PROJECT)}
    path=tmp_path/'owner.env';path.write_text('\n'.join(k+'='+v for k,v in values.items())+'\n');path.chmod(0o600)
    try:yield path
    finally:path.unlink()


def run(owner_file,*args):
    result=subprocess.run([sys.executable,'-m','backend.app.management','--env-file',str(owner_file),*args],capture_output=True,text=True)
    assert result.returncode==0,result.stdout
    return json.loads(result.stdout)['result']


def protected_input(owner_file,args,password):
    master,slave=pty.openpty()
    process=subprocess.Popen([sys.executable,'-m','backend.app.management','--env-file',str(owner_file),*args],stdin=slave,stdout=slave,stderr=slave)
    os.close(slave);output=b'';sent=0;deadline=time.monotonic()+15
    prompts=['새 비밀번호: '.encode(),'새 비밀번호 확인: '.encode()]
    try:
        while time.monotonic()<deadline:
            if select.select([master],[],[],.1)[0]:
                try:chunk=os.read(master,65536)
                except OSError:break
                if not chunk:break
                output+=chunk
                if sent<2 and prompts[sent] in output:
                    os.write(master,password.encode()+b'\n');sent+=1
            if process.poll() is not None:break
        assert process.wait(timeout=3)==0,output.decode(errors='replace')
        assert sent==2 and password.encode() not in output
        source=output.decode(errors='replace');start=source.index('{"status":')
        return json.loads(source[start:].strip())['result']
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=3)
        os.close(master)


def test_real_cli_bootstrap_protected_creation_reset_email_membership_activation(auth_database,owner_file):
    db,owner,manager,settings,setup=auth_database
    assert run(owner_file,'bootstrap-development')==setup
    email=uuid4().hex+'@example.com'
    user=protected_input(owner_file,['create-email-user','--email',email,'--display-name','CLI 합성 사용자'],PASSWORD)
    assert user['email']==email
    protected_input(owner_file,['reset-password','--user-id',user['id']],PASSWORD+' new')
    changed=uuid4().hex+'@example.com'
    run(owner_file,'change-email','--user-id',user['id'],'--email',changed)
    run(owner_file,'set-project-member','--project-id',str(DEV_PROJECT),'--user-id',user['id'],'--role','viewer','--active','true')
    run(owner_file,'set-user-active','--user-id',user['id'],'--active','false')
    run(owner_file,'set-user-active','--user-id',user['id'],'--active','true')
    # Remove only the synthetic extra membership so bootstrap replay retains its original configuration.
    with owner.begin() as connection:connection.exec_driver_sql('DELETE FROM public.project_members WHERE project_id=%s AND user_id=%s',(DEV_PROJECT,UUID(user['id'])))


def test_cli_never_accepts_password_argv_or_noninteractive_secret_input(owner_file):
    result=subprocess.run([sys.executable,'-m','backend.app.management','--env-file',str(owner_file),'create-email-user',
        '--email','synthetic@example.com','--display-name','synthetic','--password','sensitive-synthetic-marker'],capture_output=True,text=True)
    assert result.returncode==1 and 'sensitive-synthetic-marker' not in result.stdout+result.stderr
    result=subprocess.run([sys.executable,'-m','backend.app.management','--env-file',str(owner_file),'create-email-user',
        '--email','synthetic@example.com','--display-name','synthetic'],input=PASSWORD+'\n',capture_output=True,text=True)
    assert result.returncode==1 and json.loads(result.stdout)['code']=='INTERACTIVE_PASSWORD_REQUIRED'
    assert PASSWORD not in result.stdout+result.stderr
