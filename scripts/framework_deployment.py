"""Prepare private, role-scoped AWS runtime files without deploying anything.

Run from the repository: python -m scripts.framework_deployment --help.
This command never connects to AWS, PostgreSQL, Neo4j or a model server.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil

from backend.app.config import ConfigurationError, read_environment_file
from backend.app.v1_config import load_v1_settings, is_loopback
from backend.runtime_environment import runtime_values
from backend.agent_framework.composition import load_config, TeamConfiguration
from backend.agent_framework.deployment import require_live_configuration, require_verified_database

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'deploy/qwen-a02/worker-config.rag.example.json'
QWEN = 'http://qwen-tunnel:18015/v1'
PROXY = '172.30.66.10'


def private_file(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        os.chmod(path, 0o600)
        stream.write(value)


def literal_env(values):
    # These files are parsed by our literal parser, not a shell or Compose.
    if any(any(c in str(v) for c in '\x00\r\n') for v in values.values()):
        raise ConfigurationError('Runtime values must be single-line literals.')
    return ''.join(f"{key}='{value}'\n" for key, value in sorted(values.items()))


def prepare(args):
    if args.output_dir.exists():
        raise ConfigurationError('Output directory already exists; no files were overwritten.')
    host = args.public_host.lower()
    if (len(host) > 253 or '.' not in host
            or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                       for label in host.split('.'))):
        raise ConfigurationError('Select an exact public DNS hostname without scheme or port.')
    data_root = PurePosixPath(args.data_root)
    if not data_root.is_absolute() or '..' in data_root.parts or data_root == PurePosixPath('/'):
        raise ConfigurationError('Select a dedicated absolute Linux data directory.')
    for path in (args.ca_file, args.identity, args.known_hosts):
        if not path.is_file() or path.stat().st_size == 0:
            raise ConfigurationError('Provide nonempty CA, server SSH identity and known-hosts files.')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+', args.ssh_host) or args.ssh_host.startswith('-'):
        raise ConfigurationError('Invalid RunPod SSH host.')
    if not 1 <= args.ssh_port <= 65535:
        raise ConfigurationError('Invalid RunPod SSH port.')

    values = read_environment_file(args.env_file)
    # Validate the explicit service file and preserve process-env precedence.
    for role in ('v1', 'framework-worker', 'framework-projection'):
        runtime_values(role, args.env_file)
    settings = load_v1_settings(values)
    if settings.environment != 'production' or settings.origins != ('https://' + host,):
        raise ConfigurationError('Use an explicit production file with the selected HTTPS origin.')
    require_verified_database(settings)
    if is_loopback(settings.database.host) or settings.database.host == 'host.docker.internal':
        raise ConfigurationError('Select the remote deployment database, not a desktop loopback address.')
    if settings.database.sslrootcert != '/run/certs/rds-ca.pem':
        raise ConfigurationError('Deployment DB_SSLROOTCERT must be /run/certs/rds-ca.pem.')
    if values.get('CC_QWEN_BASE_URL', '') not in ('', QWEN):
        raise ConfigurationError('This deployment uses the private qwen-tunnel endpoint.')
    key = values.get('CC_QWEN_API_KEY') or values.get('A02_QWEN_API_KEY')
    if not key or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ConfigurationError('Provide the existing RunPod server API key in the private input file.')
    if any(values.get(k) not in (None, '', key) for k in ('CC_QWEN_API_KEY', 'A02_QWEN_API_KEY')):
        raise ConfigurationError('A02 and shared model credentials must match the selected server.')
    graph_password = values.get('CC_NEO4J_PASSWORD', '')
    if (len(graph_password) < 8 or graph_password.startswith('change-me-')
            or any(c in graph_password for c in '\x00\r\n')):
        raise ConfigurationError('Provide a separate deployment Neo4j password.')
    if values.get('CC_NEO4J_USER', 'neo4j') != 'neo4j':
        raise ConfigurationError('The initial deployment Neo4j user must be neo4j.')

    configuration = load_config(args.config)
    require_live_configuration(configuration)
    if values.get('CC_QWEN_MODEL', configuration.pdf_extraction.model) != configuration.pdf_extraction.model:
        raise ConfigurationError('All model adapters must select the same configured Qwen model.')
    raw = configuration.model_dump(mode='json')
    raw['pdf_extraction']['endpoint'] = QWEN + '/chat/completions'
    configuration = TeamConfiguration.model_validate(raw)
    values.update(CC_QWEN_BASE_URL=QWEN, CC_QWEN_API_KEY=key, A02_QWEN_API_KEY=key,
                  CC_QWEN_MODEL=configuration.pdf_extraction.model,
                  CC_NEO4J_URI='bolt://neo4j:7687', CC_NEO4J_USER='neo4j',
                  TRUSTED_PROXY_IPS=PROXY)
    scoped = {role: runtime_values(component, environ=values) for role, component in (
        ('api', 'v1'), ('worker', 'framework-worker'), ('graph', 'framework-projection'))}
    # Reject placeholders before making any output directory.
    if any(secret.startswith('change-me-') for secret in (values['CSRF_SECRET'], values['CURSOR_SECRET'])):
        raise ConfigurationError('Replace deployment browser secrets before preparation.')
    target = args.output_dir.resolve()
    compose = {
        'AF_RUNTIME_DIR': str(target), 'AF_DATA_ROOT': str(data_root),
        'AF_TLS_DIRECTORY': str(args.tls_directory.resolve()), 'AF_RDS_CA_FILE': str(args.ca_file.resolve()),
        'AF_SSH_KEY_FILE': str(args.identity.resolve()), 'AF_KNOWN_HOSTS_FILE': str(args.known_hosts.resolve()),
        'AF_RUNPOD_HOST': args.ssh_host, 'AF_RUNPOD_PORT': str(args.ssh_port),
        'AF_PUBLIC_HOST': host, 'AF_FRAMEWORK_IMAGE': args.image, 'AF_WEB_IMAGE': args.web_image,
        'AF_TUNNEL_IMAGE': args.tunnel_image,
    }
    if any(any(c in v for c in '\x00\r\n') for v in compose.values()):
        raise ConfigurationError('Deployment paths and options must be single-line values.')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the destination before writing; never replace an existing path.
    target.mkdir(mode=0o700)
    try:
        for role, mapping in scoped.items():
            private_file(target / (role + '.env'), literal_env(mapping))
        private_file(target / 'neo4j-password', graph_password)
        private_file(target / 'team.json', configuration.model_dump_json(indent=2) + '\n')
        # Compose's env parser uses backslash-escaped single quotes, unlike ours.
        encoded = ''.join(k + "='" + v.replace("'", "\\'") + "'\n" for k, v in compose.items())
        private_file(target / 'compose.env', encoded)
        private_file(target / 'preparation.json', json.dumps({
            'contract_version': '3.0.0', 'context_preparation': 'neo4j_rag',
            'source_config': str(args.config.resolve()), 'public_host': host,
            'deployment_performed': False, 'model_connection_verified': False,
            'rds_connection_verified': False, 'secrets_in_compose_environment': False,
        }, ensure_ascii=False, indent=2) + '\n')
    except BaseException:
        # Only this command's exclusively created, unused output is removed.
        shutil.rmtree(target)
        raise
    return target


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--public-host', required=True)
    p.add_argument('--config', type=Path, default=CONFIG)
    p.add_argument('--data-root', default='/srv/creative-checker')
    p.add_argument('--tls-directory', type=Path, required=True)
    p.add_argument('--ca-file', type=Path, required=True)
    p.add_argument('--identity', type=Path, required=True)
    p.add_argument('--known-hosts', type=Path, required=True)
    p.add_argument('--ssh-host', required=True)
    p.add_argument('--ssh-port', type=int, default=20719)
    p.add_argument('--image', default='creative-checker-framework:aws')
    p.add_argument('--web-image', default='creative-checker-web:aws')
    p.add_argument('--tunnel-image', default='creative-checker-qwen-tunnel:aws')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        target = prepare(args)
    except ConfigurationError as error:
        print(json.dumps({'error': 'DEPLOYMENT_PREPARATION_REJECTED',
                          'message': str(error), 'files_overwritten': False}))
        return 2
    except (OSError, ValueError):
        print(json.dumps({'error': 'DEPLOYMENT_PREPARATION_REJECTED', 'files_overwritten': False}))
        return 2
    print(json.dumps({'prepared': str(target), 'deployment_performed': False, 'source_env_modified': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
