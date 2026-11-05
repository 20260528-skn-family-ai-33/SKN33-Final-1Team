"""Test target attestation, credential isolation and owned-resource cleanup."""
import json
from types import SimpleNamespace

import pytest

from scripts import framework_graph_test as runner
from tests.agent_framework.graph_test_environment import isolated_graph_settings,UnsafeGraphTestTarget


def test_inherited_development_and_test_credentials_are_not_reused():
    original={'PATH':'Windows path & 팀','CC_NEO4J_URI':'bolt://localhost:57631',
        'CC_NEO4J_PASSWORD':'development-secret','DATABASE_URL':'development-db',
        'DB_OWNER_PASSWORD':'owner-secret','TEST_DB_ENV_FILE':'old.env',
        'AF_TEST_NEO4J_URI':'bolt://localhost:57631','AF_ISOLATION_RUN_ID':'old',
        'COMPOSE_PROJECT_NAME':'development','PYTEST_ADDOPTS':'-m live_model',
        'A02_QWEN_API_KEY':'model-secret','AWS_SECRET_ACCESS_KEY':'aws-secret',
        'FRAMEWORK_DOCKER_TEST_IMAGE':'example:test'}
    assert runner.clean_environment(original)=={'PATH':'Windows path & 팀','FRAMEWORK_DOCKER_TEST_IMAGE':'example:test'}
    assert original['CC_NEO4J_PASSWORD']=='development-secret'


def test_explicit_compose_project_and_literal_paths_do_not_use_a_shell(tmp_path,monkeypatch):
    folder=tmp_path/'한글 & team';folder.mkdir()
    databases=runner.DisposableDatabases(folder,{'PATH':'tools','COMPOSE_FILE':'do-not-use.yaml'})
    seen=[]
    def call(command,**kwargs):seen.append((command,kwargs));return SimpleNamespace(returncode=0,stdout='ok')
    monkeypatch.setattr(runner.subprocess,'run',call)
    databases.compose('up','--detach')
    command,kwargs=seen[0]
    assert kwargs['shell'] is False and kwargs['cwd']==runner.ROOT
    assert kwargs['env']=={'PATH':'tools'}
    assert command[command.index('--file')+1]==str(runner.COMPOSE)
    assert command[command.index('--env-file')+1]==str(folder/'compose.env')
    assert command[command.index('--project-name')+1].startswith('cc-af-test-')
    assert databases.graph_password not in ' '.join(command)
    assert databases.admin_password not in ' '.join(command)
    assert '\r' not in (folder/'compose.env').read_text()


def test_two_test_runs_have_independent_projects_and_secrets(tmp_path):
    one=tmp_path/'one';two=tmp_path/'two';one.mkdir();two.mkdir()
    a=runner.DisposableDatabases(one,{});b=runner.DisposableDatabases(two,{})
    assert a.project!=b.project and a.graph_password!=b.graph_password and a.token!=b.token


def test_cleanup_refuses_unowned_resources(tmp_path,monkeypatch):
    databases=runner.DisposableDatabases(tmp_path,{});databases.started=True;calls=[]
    monkeypatch.setattr(databases,'resources',lambda:[('container','test-container')])
    monkeypatch.setattr(databases,'command',lambda *args:json.dumps({runner.RUN_LABEL:'another-run',runner.ENV_LABEL:'test'}))
    monkeypatch.setattr(databases,'compose',lambda *args:calls.append(args))
    with pytest.raises(runner.IsolatedTestError,match='REFUSED_UNOWNED'):databases.cleanup()
    assert calls==[]


def test_cleanup_is_limited_to_the_owned_compose_project(tmp_path,monkeypatch):
    databases=runner.DisposableDatabases(tmp_path,{});databases.started=True;calls=[]
    resources=[('container','test-container'),('volume','test-volume'),('network','test-network')]
    monkeypatch.setattr(databases,'resources',lambda:list(resources))
    monkeypatch.setattr(databases,'command',lambda *args:json.dumps({runner.RUN_LABEL:databases.project,runner.ENV_LABEL:'test'}))
    def down(*args):calls.append(args);resources.clear()
    monkeypatch.setattr(databases,'compose',down)
    databases.cleanup()
    assert calls==[('down','--volumes','--timeout','10')] and not databases.started


def test_pytest_failure_still_cleans_owned_resources(monkeypatch):
    events=[]
    class Databases:
        test_environment={}
        def __init__(self,*_):pass
        def start(self):events.append('start')
        def initialize(self):events.append('initialize')
        def cleanup(self):events.append('cleanup')
    monkeypatch.setattr(runner,'DisposableDatabases',Databases)
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=7))
    assert runner.run_tests(['tests/example.py'],host=True)==7
    assert events==['start','initialize','cleanup']


def test_interrupt_after_partial_startup_still_cleans_owned_resources(monkeypatch):
    events=[]
    class Databases:
        def __init__(self,*_):pass
        def start(self):events.append('start');raise KeyboardInterrupt
        def cleanup(self):events.append('cleanup')
    monkeypatch.setattr(runner,'DisposableDatabases',Databases)
    with pytest.raises(KeyboardInterrupt):runner.run_tests(['tests/example.py'],host=True)
    assert events==['start','cleanup']


def test_missing_test_attestation_fails_before_any_graph_access():
    def unexpected(*_):pytest.fail('must not create a graph client')
    with pytest.raises(UnsafeGraphTestTarget):isolated_graph_settings({},unexpected)


@pytest.mark.parametrize('matches',[0,2])
def test_unattested_or_ambiguous_graph_is_read_only_and_rejected(matches):
    queries=[];closed=[]
    class Session:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def run(self,query,**params):queries.append(query);return SimpleNamespace(single=lambda:{'count':matches})
    class Graph:
        def __init__(self,*_):pass
        def _session(self):return Session()
        def close(self):closed.append(True)
    env={'AF_TEST_NEO4J_URI':'bolt://localhost:57631','AF_TEST_NEO4J_PASSWORD':'not-printed',
        'AF_TEST_NEO4J_RUN_ID':'cc-af-test-012345abcdef','AF_TEST_NEO4J_TOKEN':'test-token'}
    with pytest.raises(UnsafeGraphTestTarget):isolated_graph_settings(env,Graph)
    assert len(queries)==1 and queries[0].startswith('MATCH') and closed==[True]


def test_host_port_must_be_loopback(tmp_path,monkeypatch):
    databases=runner.DisposableDatabases(tmp_path,{})
    monkeypatch.setattr(databases,'compose',lambda *_:'0.0.0.0:12345')
    with pytest.raises(runner.IsolatedTestError,match='NON_LOOPBACK'):databases.port('neo4j',7687)


def test_host_tests_keep_loopback_and_pass_the_owned_network_to_container_tests(tmp_path,monkeypatch):
    from scripts import framework_graph_test_container
    databases=runner.DisposableDatabases(tmp_path,{});databases.pg_port='12345';databases.graph_port='12346'
    seen=[]
    monkeypatch.setattr(framework_graph_test_container,'initialize',lambda env:seen.append(env))
    databases.initialize()
    env,=seen
    assert env['TEST_DB_HOST']=='127.0.0.1' and env['TEST_DB_PORT']=='12345'
    assert env['AF_TEST_DOCKER_NETWORK']==databases.project+'_default'
    assert 'AF_TEST_DOCKER_NETWORK' not in databases.environment


def test_default_command_runs_pytest_inside_linux_container(monkeypatch):
    events=[];seen=[]
    class Databases:
        project='cc-af-test-012345abcdef';graph_port='12345';pg_port='12346';environment={'PATH':'tools'}
        def __init__(self,*_):pass
        def build(self):events.append('build')
        def start(self):events.append('start')
        def initialize(self):pytest.fail('host DB clients must not be used by default')
        def compose_argv(self,*args):return ['docker','compose',*args]
        def cleanup(self):events.append('cleanup')
    def run(command,**kwargs):seen.append((command,kwargs));return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner,'DisposableDatabases',Databases)
    monkeypatch.setattr(runner.subprocess,'run',run)
    assert runner.run_tests(['tests/example.py','-q'])==0
    assert events==['build','start','cleanup']
    assert seen[0][0]==['docker','compose','run','--rm','--no-deps','runner','tests/example.py','-q','-m','db_integration']
    assert seen[0][1]['shell'] is False
