"""Expire idle AWS_TEST reviews and prepare their Nginx rollout exceptions."""
import argparse
from datetime import timedelta
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.request import Request, urlopen


WAITING = ('waiting_criteria', 'waiting_asset', 'waiting_review', 'waiting_revision')
MAX_IDLE = timedelta(hours=24)
BATCH_SIZE = 64
EXPIRED_CODE = 'REVIEW_EXPIRED'
ROLLOUT_FILE = '/etc/nginx/conf.d/creative-checker-framework-d52557f-rollout.conf'
TEST_HOST = 'd3navmtcjn23y2.cloudfront.net'
MAP_HEADER = 'map "$cc_a02_backend_port:$request_method:$uri" $cc_a02_rollout_port {'
TEST_FALLBACK = re.compile(r'^([ \t]*)~\^18016:[ \t]+([0-9]+);[ \t]*$', re.M)
MAIN_FALLBACK = re.compile(r'^[ \t]*~\^8018:[ \t]+([0-9]+);[ \t]*$', re.M)
TEST_EXCEPTION = re.compile(
    r'^[ \t]*~\^18016:POST:/api/framework/v1/runs/[^\n]+?[ \t]+([0-9]+);[ \t]*\n?', re.M
)
MAIN_EXCEPTION = re.compile(r'^[ \t]*~\^8018:POST:[^\n]+?[ \t]+([0-9]+);[ \t]*$', re.M)
ROUTE_ACTIONS = '(?:approvals|criteria-versions|criteria-approvals|criteria-additions)'
RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}')


def waiting_reviews(scope, *, include_processing=False):
    from sqlalchemy import and_, exists, not_, or_, select
    from backend.agent_framework.postgres import runs

    config_hash, runtime_hash = scope
    state = runs.c.snapshot_json['execution_status'].as_string()
    child = runs.alias('child')
    continued = exists(select(child.c.run_id).where(or_(
        and_(state == 'waiting_asset',
             child.c.input_json['baseline']['source_run_id'].as_string() == runs.c.run_id),
        and_(state == 'waiting_revision', child.c.parent_run_id == runs.c.run_id)
    ))).correlate(runs)
    return (
        runs.c.configuration_sha256 == config_hash,
        runs.c.runtime_sha256 == runtime_hash,
        state.in_(WAITING + ('queued', 'extracting', 'reviewing') if include_processing else WAITING),
        runs.c.input_json['criteria_addition'].as_string().is_(None),
        not_(continued),
    )


def expiration_conditions(scope, cutoff):
    from sqlalchemy import exists, not_, or_, select
    from backend.agent_framework.criteria_review import versions
    from backend.agent_framework.postgres import jobs, runs

    addition = runs.alias('addition')
    pending = exists(select(jobs.c.job_id).select_from(jobs.join(addition, addition.c.run_id == jobs.c.run_id))
        .where(jobs.c.state.in_(('queued', 'running')), or_(
            jobs.c.run_id == runs.c.run_id,
            addition.c.input_json['criteria_addition']['source_run_id'].as_string() == runs.c.run_id,
        ))).correlate(runs)
    edited = exists(select(versions.c.criteria_version_id).where(
        versions.c.source_run_id == runs.c.run_id, versions.c.created_at > cutoff
    )).correlate(runs)
    return (*waiting_reviews(scope), runs.c.updated_at <= cutoff, not_(pending), not_(edited))


def expire_reviews(store, scope, *, apply=False):
    from sqlalchemy import func, select
    from backend.agent_framework.contracts import FrameworkError, RunSnapshot
    from backend.agent_framework.postgres import runs
    from backend.app.persistence.transaction import unit_of_work

    error = FrameworkError(
        code=EXPIRED_CODE, message='24시간 동안 진행되지 않아 검수가 만료됐어요. 새 검수를 시작해 주세요.'
    )
    with unit_of_work(store.engine) as db:
        if not apply:
            db.exec_driver_sql('SET TRANSACTION READ ONLY')
        cutoff = db.execute(select(func.clock_timestamp())).scalar_one() - MAX_IDLE
        conditions = expiration_conditions(scope, cutoff)
        if not apply:
            return db.execute(select(func.count()).select_from(runs).where(*conditions)).scalar_one()

    expired = 0
    while True:
        with unit_of_work(store.engine) as db:
            ids = db.execute(select(runs.c.run_id).where(*conditions)
                .order_by(runs.c.updated_at, runs.c.run_id).limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)).scalars().all()
            if not ids:
                return expired
            # After locking, use a new statement so a just-committed edit or child is visible.
            rows = db.execute(select(runs).where(runs.c.run_id.in_(ids), *conditions)).mappings()
            for row in rows:
                snapshot = RunSnapshot.model_validate(row['snapshot_json'])
                store._save(db, snapshot.model_copy(update={'execution_status': 'failed', 'error': error}))
                store.event(db, row['run_id'], 'run.failed', code=EXPIRED_CODE)
                store.event(db, row['run_id'], 'history.visibility_changed',
                            actor_id=str(row['actor_id']), hidden=True)
                expired += 1


def rollout_ids(store, scope, *, preview=False):
    from sqlalchemy import and_, func, not_, select
    from backend.agent_framework.postgres import runs
    from backend.app.persistence.transaction import unit_of_work

    with unit_of_work(store.engine) as db:
        db.exec_driver_sql('SET TRANSACTION READ ONLY')
        conditions = waiting_reviews(scope, include_processing=True)
        if preview:
            cutoff = db.execute(select(func.clock_timestamp())).scalar_one() - MAX_IDLE
            conditions += (not_(and_(*expiration_conditions(scope, cutoff))),)
        return db.execute(select(runs.c.run_id).where(*conditions).order_by(runs.c.run_id)).scalars().all()


def database_command(args):
    from backend.agent_framework.composition import load_config
    from backend.agent_framework.postgres_commands import _values
    from backend.app.persistence.database import create_app_engine
    from backend.agent_framework.postgres import PostgresStore

    _, settings = _values(args.env_file)
    engine = create_app_engine(settings)
    try:
        store = PostgresStore(engine, load_config(args.config))
        result = {}
        for port, scope in json.loads(args.database_scopes).items():
            count = expire_reviews(store, scope, apply=args.apply)
            result[port] = {'expired' if args.apply else 'eligible': count}
            if args.rollout_port is not None:
                result[port]['run_ids'] = rollout_ids(store, scope, preview=not args.apply)
        print(json.dumps(result))
    finally:
        engine.dispose()


def routing_ports(config):
    fallback = TEST_FALLBACK.findall(config)
    main = MAIN_FALLBACK.findall(config)
    if config.count(MAP_HEADER) != 1 or len(fallback) != 1 or len(main) != 1:
        raise ValueError('Expected one AWS_TEST and one main fallback in the rollout map.')
    current = int(fallback[0][1])
    test_ports = {current, *map(int, TEST_EXCEPTION.findall(config))}
    main_ports = {8018, *map(int, main), *map(int, MAIN_EXCEPTION.findall(config))}
    return current, test_ports, main_ports


def render_rollout(config, exceptions, new_port):
    retained = TEST_EXCEPTION.sub(
        lambda match: '' if int(match[1]) in exceptions else match[0], config
    )
    fallback = TEST_FALLBACK.search(retained)
    indent = fallback[1]
    rules = []
    for port, run_ids in sorted(exceptions.items()):
        for run_id in run_ids:
            if not RUN_ID.fullmatch(run_id):
                raise ValueError('Unexpected run ID in the rollout exception list.')
            rules.append(f'{indent}~^18016:POST:/api/framework/v1/runs/{re.escape(run_id)}/'
                         f'{ROUTE_ACTIONS}/?$ {port};')
    rules.append(f'{indent}~^18016: {new_port};')
    return TEST_FALLBACK.sub(lambda _: '\n'.join(rules), retained)


def command(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def api_containers(ports):
    containers = {}
    ids = command('docker', 'ps', '-q').splitlines()
    if not ids:
        raise ValueError('No running API containers were found.')
    details = command('docker', 'inspect', '--format',
                      '{{json .Id}} {{json .HostConfig.NetworkMode}} {{json .Args}}', *ids)
    for line in details.splitlines():
        identifier, network, arguments = line.split(' ', 2)
        args = json.loads(arguments)
        if json.loads(network) != 'host' or 'postgres-serve' not in args or '--port' not in args:
            continue
        port = int(args[args.index('--port') + 1])
        if port not in ports:
            continue
        if port in containers:
            raise ValueError(f'Multiple framework API containers declare port {port}.')
        containers[port] = (json.loads(identifier), args[args.index('--env-file') + 1],
                            args[args.index('--config') + 1])
    missing = ports - containers.keys()
    if missing:
        raise ValueError(f'Running framework API containers missing for ports {sorted(missing)}.')
    return containers


def fingerprint(container):
    identifier, _, config = container
    values = command(
        'docker', 'exec', identifier, '/app/.venv/bin/python', '-c',
        'import sys; from backend.agent_framework.composition import load_config; '
        'from backend.agent_framework.postgres import digest,runtime_revision; '
        'print(digest(load_config(sys.argv[1])),runtime_revision())', config
    ).split()
    if len(values) != 2 or any(not re.fullmatch('[a-f0-9]{64}', value) for value in values):
        raise ValueError('Unexpected framework API fingerprint.')
    return tuple(values)


def check_health(port):
    request = Request(f'http://127.0.0.1:{port}/api/framework/v1/health',
                      headers={'Host': TEST_HOST, 'X-Forwarded-Proto': 'https'})
    with urlopen(request, timeout=5) as response:
        if json.load(response).get('poster_review_ready') is not True:
            raise ValueError(f'Port {port} is not ready for poster review.')


def replace_nginx(path, original, rendered):
    # Backup files live outside conf.d so the active include glob never loads them.
    backup_dir = Path(tempfile.mkdtemp(prefix='aws-test-review-rollout.', dir=path.parent.parent))
    backup = backup_dir / 'original.conf'
    staged = backup_dir / 'new.conf'
    shutil.copy2(path, backup)
    shutil.copy2(path, staged)
    staged.write_text(rendered)
    if path.read_text() != original or backup.read_text() != original:
        raise ValueError('Nginx rollout file changed during the preview.')
    staged.replace(path)
    try:
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    except subprocess.CalledProcessError:
        restore = backup_dir / 'restore.conf'
        shutil.copy2(backup, restore)
        restore.replace(path)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        raise
    return str(backup)


def host_command(args):
    path = Path(args.nginx_config).resolve(strict=True)
    config = path.read_text()
    current, test_ports, main_ports = routing_ports(config)
    new_port = args.rollout_port
    containers = api_containers(test_ports | main_ports | ({new_port} if new_port else set()))
    hashes = {port: fingerprint(container) for port, container in containers.items()}
    main_hashes = {hashes[port] for port in main_ports}
    # Shared fingerprints cannot identify the site that created a row.
    exclusive = {port: hashes[port] for port in sorted(test_ports - main_ports)
                 if hashes[port] not in main_hashes}
    if len(set(exclusive.values())) != len(exclusive):
        raise ValueError('Multiple AWS_TEST API ports have the same fingerprint. Review their routes first.')
    if not exclusive:
        raise ValueError('No AWS_TEST-only runtime scope was found.')
    if new_port is not None:
        previous_hashes = {hashes[port] for port in test_ports | main_ports}
        if new_port in test_ports | main_ports or hashes[new_port] in previous_hashes:
            raise ValueError('Rollout requires a new port and a new AWS_TEST API fingerprint.')
        check_health(new_port)
    identifier, env_file, team = containers[current]
    db_args = ['--database-scopes', json.dumps(exclusive), '--env-file', env_file, '--config', team]
    if args.apply:
        db_args.append('--apply')
    if new_port is not None:
        db_args.extend(('--rollout-port', str(new_port)))
    result = json.loads(command('docker', 'exec', '-i', identifier, '/app/.venv/bin/python', '-', *db_args,
                                input=Path(__file__).read_text()))
    summary = {'by_port': result, 'preserved_ports': sorted(test_ports - exclusive.keys())}
    if new_port is not None:
        exceptions = {int(port): info.pop('run_ids') for port, info in result.items()}
        for port, ids in exceptions.items():
            result[str(port)]['exceptions'] = len(ids)
        rendered = render_rollout(config, exceptions, new_port)
        summary['rollout_port'] = new_port
        if args.apply:
            summary['backup'] = replace_nginx(path, config, rendered)
    print(json.dumps(summary))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx-config', default=ROLLOUT_FILE)
    parser.add_argument('--rollout-port', type=int, help='New AWS_TEST API port after an image deployment.')
    parser.add_argument('--apply', action='store_true', help='Persist changes. The default is read-only.')
    parser.add_argument('--database-scopes', help=argparse.SUPPRESS)
    parser.add_argument('--env-file', help=argparse.SUPPRESS)
    parser.add_argument('--config', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.database_scopes:
        database_command(args)
    elif args.apply:
        import fcntl

        with open('/run/lock/creative-checker-aws-test-review-expiry.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            host_command(args)
    else:
        host_command(args)


if __name__ == '__main__':
    main()
