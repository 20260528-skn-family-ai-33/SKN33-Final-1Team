"""Offline deployment preparation and HTTPS boundaries; no AWS, DB or model calls."""
from dataclasses import replace
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.app.config import ConfigurationError, database_tls, load_settings, read_environment_file
from backend.app.v1_config import load_v1_settings
from backend.agent_framework.composition import load_config
from backend.agent_framework.deployment import require_live_configuration, trusted_proxies
from backend.agent_framework.postgres_api import create_postgres_app
from backend.runtime_environment import MANAGED, MANAGED_PREFIXES, runtime_values
from scripts import framework_deployment as deploy
from tests.app.auth_fixtures import auth_environment


@pytest.fixture
def prepared_input(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key in MANAGED or key.startswith(MANAGED_PREFIXES):
            monkeypatch.delenv(key)
    folder = tmp_path / '배포 & team space'
    folder.mkdir()
    for name in ('rds-ca.pem', 'identity', 'known_hosts'):
        (folder / name).write_text('synthetic fixture only', encoding='utf-8')
    values = auth_environment(APP_ENV='production', LOCAL_ADMIN_LOGIN='0', DEV_PROJECT_ID='',
        DB_HOST='synthetic-db.example.test',
        AUTH_ALLOWED_ORIGINS='https://checker.example.test', AUTH_ALLOWED_HOSTS='checker.example.test',
        DB_SSLMODE='verify-full', DB_SSLROOTCERT='/run/certs/rds-ca.pem',
        A02_QWEN_API_KEY="synthetic-model-$#-key'with-quote",
        CC_NEO4J_USER='neo4j', CC_NEO4J_PASSWORD='synthetic-graph-private',
        CC_QWEN_MODEL='Qwen/Qwen3.5-4B')
    env = folder / 'private.env'
    env.write_text(deploy.literal_env(values), encoding='utf-8')
    args = deploy.parser().parse_args([
        '--env-file', str(env), '--output-dir', str(folder / 'prepared'),
        '--public-host', 'checker.example.test', '--tls-directory', str(folder / 'tls'),
        '--ca-file', str(folder / 'rds-ca.pem'), '--identity', str(folder / 'identity'),
        '--known-hosts', str(folder / 'known_hosts'), '--ssh-host', 'example.test'])
    return args, values


def test_preparation_preserves_source_and_splits_browser_model_graph_secrets(prepared_input):
    args, values = prepared_input
    before = args.env_file.read_bytes()
    config_before = args.config.read_bytes()
    target = deploy.prepare(args)
    api, worker, graph = (read_environment_file(target / (role + '.env'), {})
                          for role in ('api', 'worker', 'graph'))
    assert api['CSRF_SECRET'] == values['CSRF_SECRET']
    assert api['TRUSTED_PROXY_IPS'] == deploy.PROXY
    assert not {'A02_QWEN_API_KEY', 'CC_QWEN_API_KEY', 'CC_NEO4J_PASSWORD'} & api.keys()
    assert not {'CSRF_SECRET', 'CURSOR_SECRET', 'AUTH_ALLOWED_ORIGINS'} & worker.keys()
    assert worker['A02_QWEN_API_KEY'] == worker['CC_QWEN_API_KEY'] == values['A02_QWEN_API_KEY']
    assert worker['CC_QWEN_BASE_URL'] == deploy.QWEN
    assert worker['CC_NEO4J_URI'] == graph['CC_NEO4J_URI'] == 'bolt://neo4j:7687'
    assert graph['CC_NEO4J_PASSWORD'] == values['CC_NEO4J_PASSWORD']
    assert not {'A02_QWEN_API_KEY', 'CC_QWEN_API_KEY', 'CSRF_SECRET', 'CURSOR_SECRET'} & graph.keys()
    assert all(mapping['DB_SSLMODE'] == 'verify-full' for mapping in (api, worker, graph))
    team = load_config(target / 'team.json')
    require_live_configuration(team)
    assert team.pdf_extraction.endpoint == worker['CC_QWEN_BASE_URL'] + '/chat/completions'
    assert team.context_preparation.mode == 'neo4j_rag'
    assert args.env_file.read_bytes() == before and args.config.read_bytes() == config_before
    assert not json.loads((target / 'preparation.json').read_text())['deployment_performed']
    if os.name != 'nt':
        assert target.stat().st_mode & 0o077 == 0
        assert all(path.stat().st_mode & 0o077 == 0 for path in target.iterdir())


def test_existing_prepared_directory_is_never_overwritten(prepared_input):
    args, _ = prepared_input
    target = deploy.prepare(args)
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(ConfigurationError, match='already exists'):
        deploy.prepare(args)
    assert {p.name: p.read_bytes() for p in target.iterdir()} == before


@pytest.mark.parametrize('change', [
    {'APP_ENV': 'development'}, {'DB_SSLMODE': 'require'}, {'DB_SSLROOTCERT': ''},
    {'DB_HOST': '127.0.0.1'}, {'DB_HOST': 'host.docker.internal'},
    {'AUTH_ALLOWED_ORIGINS': 'http://checker.example.test'}, {'LOCAL_ADMIN_LOGIN': '1'},
    {'DB_OWNER_PASSWORD': 'synthetic-owner-must-never-enter-runtime'},
    {'A02_QWEN_API_KEY': ''}, {'CC_QWEN_API_KEY': 'a-different-server-key'},
    {'CC_QWEN_BASE_URL': 'https://unintended.example.test/v1'},
])
def test_unsafe_preparation_fails_before_writing_any_output(prepared_input, change):
    args, values = prepared_input
    args.env_file.write_text(deploy.literal_env(values | change), encoding='utf-8')
    with pytest.raises(ConfigurationError):
        deploy.prepare(args)
    assert not args.output_dir.exists()


def test_rejects_unconfigured_profile_and_missing_files(prepared_input):
    args, _ = prepared_input
    args.config = Path('deploy/qwen-a02/worker-config.presentation.example.json')
    with pytest.raises(ConfigurationError, match='real PDF/poster'):
        deploy.prepare(args)
    args.ca_file.unlink()
    with pytest.raises(ConfigurationError, match='nonempty'):
        deploy.prepare(args)
    assert not args.output_dir.exists()


@pytest.mark.parametrize('host', ['https://checker.example.test', 'checker.example.test;command',
                                 '*.example.test', 'checker.example.test:443', '-bad.example.test'])
def test_public_hostname_is_not_shell_or_template_input(prepared_input, host):
    args, _ = prepared_input
    args.public_host = host
    with pytest.raises(ConfigurationError):
        deploy.prepare(args)


def test_production_api_enforces_https_without_connecting_to_a_database(prepared_input, monkeypatch):
    _, values = prepared_input
    settings = load_v1_settings(values)
    app = create_postgres_app(settings, load_config(deploy.CONFIG), engine=object())
    # Exercise the real HTTP boundary without starting the database lifespan.
    app.state.ready = True
    app.state.engine = object()
    from backend.app.management import maintenance
    monkeypatch.setattr(maintenance, 'active', lambda engine: False)
    app.state.framework_revision = 'AF005'
    app.state.documents = app.state.criteria_review = app.state.poster_reviews = None
    with_https = TestClient(app, base_url='https://checker.example.test')
    response = with_https.get('/api/health')
    assert response.status_code == 200 and response.json()['ready']
    assert response.json()['model_connection_verified'] is False
    assert not response.json()['production_ready']
    insecure = TestClient(app, base_url='http://checker.example.test')
    assert insecure.get('/api/health', headers={'X-Forwarded-Proto': 'https'}).status_code == 400
    assert with_https.get('/api/health', headers={'Host': 'other.example.test'}).status_code == 404
    assert settings.session_cookie.startswith('__Host-') and settings.secure


def test_production_cannot_use_unverified_db_or_synthetic_roles(prepared_input):
    _, values = prepared_input
    settings = load_v1_settings(values)
    with pytest.raises(ConfigurationError):
        create_postgres_app(replace(settings, persistence=load_settings(values | {'DB_SSLMODE': 'require'})),
                            load_config(deploy.CONFIG), engine=object())
    with pytest.raises(ConfigurationError):
        create_postgres_app(settings, load_config('examples/agent_framework/team_config.synthetic.json'), engine=object())


@pytest.mark.parametrize('value', ['*', '0.0.0.0/0', '172.30.66.0/24', 'nginx', '127.0.0.1,'])
def test_proxy_trust_never_accepts_wildcards_or_networks(value):
    with pytest.raises(ConfigurationError):
        trusted_proxies(value)


def test_proxy_trust_is_explicit_and_empty_by_default():
    assert trusted_proxies('') == ''
    assert trusted_proxies('172.30.66.10, ::1') == '172.30.66.10,::1'


@pytest.mark.parametrize('values', [{'DB_SSLMODE': 'unsafe'}, {'DB_SSLROOTCERT': '/cert.pem'},
    {'DB_SSLMODE': 'verify-full'}, {'DB_SSLMODE': 'verify-full', 'DB_SSLROOTCERT': 'relative.pem'},
    {'DB_SSLMODE': 'verify-full', 'DB_SSLROOTCERT': '/certs/../private.pem'}])
def test_bad_database_tls_settings_are_rejected(values):
    with pytest.raises(ConfigurationError):
        database_tls(values)


def test_app_and_migration_engines_receive_identical_tls_options(prepared_input, monkeypatch):
    import sqlalchemy
    from backend.app.persistence.database import create_app_engine
    from backend.migrations.configuration import load_migration_settings, create_migration_engine
    _, values = prepared_input
    calls = []
    sentinel = object()
    monkeypatch.setattr(sqlalchemy, 'create_engine', lambda *a, **kw: calls.append(kw) or sentinel)
    monkeypatch.setattr(sqlalchemy.event, 'listens_for', lambda *a, **kw: lambda f: f)
    create_app_engine(load_settings(values))
    owner = load_migration_settings(values | {'DB_OWNER_USER': 'creative_checker_owner',
        'DB_OWNER_PASSWORD': 'synthetic-owner-only-not-app'})
    create_migration_engine(owner)
    assert len(calls) == 2
    for call in calls:
        assert call['connect_args']['sslmode'] == 'verify-full'
        assert call['connect_args']['sslrootcert'] == '/run/certs/rds-ca.pem'
    assert database_tls({}) == {}
    assert load_settings(auth_environment()).database.sslmode is None


def test_selected_runtime_roles_receive_database_tls(prepared_input):
    args, _ = prepared_input
    for component in ('v1', 'framework-worker', 'framework-projection'):
        mapping = runtime_values(component, args.env_file, {})
        assert mapping['DB_SSLMODE'] == 'verify-full'
        assert mapping['DB_SSLROOTCERT'] == '/run/certs/rds-ca.pem'


def test_certificate_paths_support_linux_container_and_windows_native_configuration():
    for path in ('/run/certs/rds-ca.pem', r'C:\Users\팀원\rds-ca.pem'):
        assert database_tls({'DB_SSLMODE':'verify-full','DB_SSLROOTCERT':path})['sslrootcert'] == path


def test_aws_manifest_exposes_only_https_edge_and_scopes_mounted_secrets():
    import yaml
    manifest = yaml.safe_load(Path('deploy/agent-framework/compose.aws.yaml').read_text(encoding='utf-8'))
    services = manifest['services']
    assert set(services) == {'web','api','worker','neo4j','projector','monitor','recovery','qwen-tunnel'}
    assert services['web']['ports'] == ['80:80','443:443']
    assert all(not service.get('ports') for name, service in services.items() if name != 'web')
    assert services['api']['secrets'] == ['api_env']
    assert services['worker']['secrets'] == ['worker_env']
    assert all(services[name]['secrets'] == ['graph_env'] for name in ('projector','monitor','recovery'))
    assert services['api']['volumes'] == services['worker']['volumes']
    assert services['web']['networks']['application']['ipv4_address'] == deploy.PROXY
    for name in ('api','worker','projector','monitor','recovery'):
        assert not services[name].get('environment')
        assert services[name]['read_only'] is True
    assert manifest['name'] != 'creative-checker-agent-framework'


def test_preparation_does_not_print_private_values_on_rejection(prepared_input, capsys):
    args, values = prepared_input
    args.env_file.write_text(deploy.literal_env(values | {'DB_OWNER_PASSWORD':'owner-secret-that-must-not-leak'}), encoding='utf-8')
    argv = ['--env-file',str(args.env_file),'--output-dir',str(args.output_dir),
        '--public-host',args.public_host,'--tls-directory',str(args.tls_directory),
        '--ca-file',str(args.ca_file),'--identity',str(args.identity),
        '--known-hosts',str(args.known_hosts),'--ssh-host',args.ssh_host]
    assert deploy.main(argv) == 2
    output = capsys.readouterr().out
    assert 'owner-secret-that-must-not-leak' not in output and values['A02_QWEN_API_KEY'] not in output
    assert not args.output_dir.exists()
