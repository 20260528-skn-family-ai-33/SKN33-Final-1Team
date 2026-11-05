"""Disposable PostgreSQL + Neo4j tests for Mac/Windows Docker Desktop (Linux).

Run with the project's Python environment. Existing .env files, databases and
development volumes are never selected. Only this invocation's labelled Compose
project is removed in finally, including when pytest fails or is interrupted.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
COMPOSE=ROOT/'deploy/agent-framework/compose.test.yaml'
RUN_PATTERN=r'cc-af-test-[0-9a-f]{12}'
RUN_LABEL='io.creativechecker.test-run'
ENV_LABEL='io.creativechecker.environment'
PRIVATE_PREFIXES=('AF_','CC_','DB_','POSTGRES_','TEST_DB_','PG','AWS_','AUTH_',
    'A02_','A04_A05_','OCR_','REVIEW_','REQUIREMENTS_','RUNPOD_','LOGO_','VIDEO_','MODELTEST_',
    'COMPOSE_','C12_','C13_')
PRIVATE_NAMES={'DATABASE_URL','PERSISTENCE_MODE','LOCAL_ADMIN_LOGIN','CSRF_SECRET',
    'CURSOR_SECRET','STORAGE_ROOT','IMPORT_ALLOWED_ROOT','PYTEST_ADDOPTS'}


class IsolatedTestError(RuntimeError):
    pass


def clean_environment(source):
    return {k:v for k,v in source.items() if k not in PRIVATE_NAMES and not k.startswith(PRIVATE_PREFIXES)}


def private_file(path,values):
    with path.open('x',encoding='utf-8',newline='\n') as out:
        out.write(''.join(k+'='+v+'\n' for k,v in values.items()))
    path.chmod(0o600)


def emit(event,**values):
    print(json.dumps({'event':event,**values}),flush=True)


class DisposableDatabases:
    def __init__(self,folder,environ=None):
        self.project='cc-af-test-'+secrets.token_hex(6)
        self.environment=clean_environment(os.environ if environ is None else environ)
        self.folder=Path(folder)
        self.graph_password=secrets.token_urlsafe(32)
        self.admin_password=secrets.token_urlsafe(32)
        self.owner_password=secrets.token_urlsafe(32)
        self.app_password=secrets.token_urlsafe(32)
        self.token=secrets.token_urlsafe(32)
        self.started=False
        self.image_created=False
        self.settings=self.folder/'compose.env'
        private_file(self.settings,{'AF_ISOLATION_RUN_ID':self.project,
            'AF_ISOLATION_GRAPH_PASSWORD':self.graph_password,'AF_ISOLATION_PG_PASSWORD':self.admin_password,
            'AF_ISOLATION_OWNER_PASSWORD':self.owner_password,'AF_ISOLATION_APP_PASSWORD':self.app_password,
            'AF_ISOLATION_TOKEN':self.token})

    def command(self,*args):
        try:
            result=subprocess.run(list(args),cwd=ROOT,env=self.environment,shell=False,
                capture_output=True,text=True,timeout=150)
        except (OSError,subprocess.SubprocessError):
            raise IsolatedTestError('DOCKER_COMMAND_UNAVAILABLE') from None
        if result.returncode:raise IsolatedTestError('DOCKER_COMMAND_FAILED')
        return result.stdout.strip()

    def compose_argv(self,*args):
        return ['docker','compose','--project-name',self.project,'--env-file',str(self.settings),
            '--file',str(COMPOSE),'--profile','test-runner',*args]

    def compose(self,*args):
        return self.command(*self.compose_argv(*args))

    def build(self):
        emit('isolated_test_image_building',project=self.project)
        self.compose('build','runner')
        self.image_created=True

    def resources(self):
        result=[]
        for kind,listing in [('container',['container','ls','--all']),('volume',['volume','ls']),('network',['network','ls'])]:
            ids=self.command('docker',*listing,'--quiet','--filter','label=com.docker.compose.project='+self.project).split()
            result.extend((kind,identity) for identity in ids)
        return result

    def assert_owned(self):
        if not re.fullmatch(RUN_PATTERN,self.project):raise IsolatedTestError('INVALID_TEST_PROJECT')
        for kind,identity in self.resources():
            field='.Config.Labels' if kind=='container' else '.Labels'
            labels=json.loads(self.command('docker',kind,'inspect','--format','{{json '+field+'}}',identity)) or {}
            if labels.get(RUN_LABEL)!=self.project or labels.get(ENV_LABEL)!='test':
                raise IsolatedTestError('REFUSED_UNOWNED_TEST_RESOURCE')

    def start(self):
        if self.command('docker','info','--format','{{.OSType}}')!='linux':
            raise IsolatedTestError('DOCKER_LINUX_ENGINE_REQUIRED')
        if self.resources():raise IsolatedTestError('TEST_PROJECT_ALREADY_EXISTS')
        # A partial Compose startup must still be cleaned up.
        self.started=True
        emit('isolated_databases_starting',project=self.project)
        self.compose('up','--detach','--wait','--wait-timeout','120','neo4j','postgres')
        self.assert_owned()
        self.graph_port=self.port('neo4j',7687)
        self.pg_port=self.port('postgres',5432)

    def port(self,service,target):
        address=self.compose('port',service,str(target))
        host,separator,port=address.rpartition(':')
        if not separator or host!='127.0.0.1' or not port.isdigit() or not 1<=int(port)<=65535:
            raise IsolatedTestError('NON_LOOPBACK_TEST_PORT')
        return port

    def initialize(self):
        self.graph_uri='bolt://127.0.0.1:'+self.graph_port
        values={'TEST_DB_ALLOW_CREATE':'1','TEST_DB_HOST':'127.0.0.1','TEST_DB_PORT':self.pg_port,
            'TEST_DB_ADMIN_USER':'af_test_admin','TEST_DB_ADMIN_PASSWORD':self.admin_password,
            'TEST_DB_OWNER_USER':'af_owner','TEST_DB_OWNER_PASSWORD':self.owner_password,
            'TEST_DB_APP_USER':'af_app','TEST_DB_APP_PASSWORD':self.app_password}
        self.test_environment=self.environment|values|{
            'AF_TEST_DOCKER_NETWORK':self.project+'_default',
            'AF_TEST_NEO4J_URI':self.graph_uri,'AF_TEST_NEO4J_PASSWORD':self.graph_password,
            'AF_TEST_NEO4J_RUN_ID':self.project,'AF_TEST_NEO4J_TOKEN':self.token}
        sys.path.insert(0,str(ROOT))
        from scripts.framework_graph_test_container import initialize
        try:initialize(self.test_environment)
        except Exception:raise IsolatedTestError('ISOLATED_DB_INITIALIZATION_FAILED') from None
        emit('isolated_databases_ready',project=self.project,neo4j_port=self.graph_port,postgres_port=self.pg_port)

    def cleanup(self):
        if self.started:
            self.assert_owned()
            self.compose('down','--volumes','--timeout','10')
            if self.resources():raise IsolatedTestError('TEST_RESOURCES_REMAIN')
            self.started=False
            emit('isolated_databases_removed',project=self.project)
        if self.image_created:
            labels=json.loads(self.command('docker','image','inspect','--format','{{json .Config.Labels}}',self.project+':runner'))
            if labels.get(RUN_LABEL)!=self.project or labels.get(ENV_LABEL)!='test':
                raise IsolatedTestError('REFUSED_UNOWNED_TEST_IMAGE')
            self.command('docker','image','rm',self.project+':runner')
            self.image_created=False
            emit('isolated_test_image_removed',project=self.project)


def run_tests(args,*,host=False):
    with tempfile.TemporaryDirectory(prefix='cc-framework-test-') as directory:
        databases=DisposableDatabases(directory)
        try:
            if not host:databases.build()
            databases.start()
            if host:
                databases.initialize()
                command=[sys.executable,'-m','pytest',*args,'-m','db_integration']
                env=databases.test_environment
            else:
                command=databases.compose_argv('run','--rm','--no-deps','runner',*args,'-m','db_integration')
                env=databases.environment
                emit('isolated_linux_tests_ready',project=databases.project,neo4j_port=databases.graph_port,postgres_port=databases.pg_port)
            return subprocess.run(command,cwd=ROOT,env=env,shell=False).returncode
        finally:
            databases.cleanup()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',action='store_true',help='POSIX host tests, for Linux CI Docker-CLI integration only')
    parser.add_argument('pytest_args',nargs=argparse.REMAINDER)
    parsed=parser.parse_args(argv);args=parsed.pytest_args
    if parsed.host and os.name!='posix':parser.error('--host requires POSIX; use the default Linux-container mode on Windows.')
    if args[:1]==['--']:args=args[1:]
    if not args:args=['tests/agent_framework/test_graph_postgres_integration.py','-q']
    previous=signal.signal(signal.SIGTERM,interrupted)
    try:
        return run_tests(args,host=parsed.host)
    except KeyboardInterrupt:
        emit('isolated_tests_interrupted');return 130
    except IsolatedTestError as error:
        # Command output, credentials and subprocess repr are never printed.
        emit('isolated_tests_failed',code=str(error));return 2
    except ImportError:
        emit('isolated_tests_failed',code='RUN_UV_SYNC_LOCKED_GROUP_DEV');return 2
    finally:
        signal.signal(signal.SIGTERM,previous)


def interrupted(_signal,_frame):
    raise KeyboardInterrupt


if __name__=='__main__':raise SystemExit(main())
