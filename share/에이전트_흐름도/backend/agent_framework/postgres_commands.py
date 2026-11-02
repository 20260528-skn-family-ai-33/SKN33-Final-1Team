"""Explicit app/owner separation and framework-only process startup."""
import json
from pathlib import Path
import signal
from threading import Event

from backend.app.config import load_settings,read_environment_file,ConfigurationError
from backend.app.persistence.database import create_app_engine,PersistenceError
from backend.app.persistence.schema_inventory import check_schema
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from .composition import load_config,build_agents
from .postgres import PostgresStore
from .postgres_schema import check_extension,check_app_permissions


def _values(filename):
    # An explicit file is required; never discover or modify the user's .env.
    values=read_environment_file(Path(filename))
    if any(values.get(k) for k in ('DB_OWNER_PASSWORD','POSTGRES_PASSWORD')):
        raise ConfigurationError('Runtime requires app-only credentials.')
    if values.get('APP_ENV') not in ('development','test'):
        raise ConfigurationError('Select APP_ENV=development or test explicitly.')
    settings=load_settings(values)
    if settings.persistence_mode!='postgres':raise ConfigurationError('Select PostgreSQL explicitly.')
    return values,settings


def _ready(engine,settings):
    check_schema(engine,settings)
    with unit_of_work(engine) as c:
        result=check_extension(c);check_app_permissions(c);return result


def postgres_check(args):
    _values_map,settings=_values(args.env_file)
    engine=create_app_engine(settings)
    try:print(json.dumps(_ready(engine,settings)));return 0
    finally:engine.dispose()


def postgres_serve(args):
    from backend.app.v1_config import load_v1_settings
    from .postgres_api import create_postgres_app
    import uvicorn
    values,_settings=_values(args.env_file)
    app=create_postgres_app(load_v1_settings(values),load_config(args.config))
    uvicorn.run(app,host='127.0.0.1',port=args.port,workers=1,reload=False)
    return 0


def postgres_worker(args):
    from .durable_worker import PostgresQueue,execute_claim
    _values_map,settings=_values(args.env_file)
    configuration=load_config(args.config)
    engine=create_app_engine(settings)
    stop=Event()
    for signum in (signal.SIGTERM,signal.SIGINT):signal.signal(signum,lambda *_:stop.set())
    try:
        _ready(engine,settings)
        store=PostgresStore(engine,configuration)
        queue=PostgresQueue(store)
        agents=build_agents(configuration)  # Only worker initializes role factories.
        print(json.dumps({'event':'framework_worker_ready'}),flush=True)
        while not stop.is_set():
            try:
                recovered=queue.recover_expired()
                claim=queue.claim_next()
                outcome=execute_claim(queue,claim,agents,stop=stop) if claim else 'idle'
                if claim or recovered or args.once:
                    print(json.dumps({'event':'framework_worker_cycle','outcome':outcome,'recovered':len(recovered)}),flush=True)
                if args.once:return 1 if outcome in ('failed','outcome_unknown','database_unavailable','authority_lost') else 0
            except (PersistenceError,AuthError):
                print(json.dumps({'event':'framework_worker_database_or_authority_unavailable'}),flush=True)
                if args.once:return 1
            stop.wait(1)
        return 0
    finally:engine.dispose()


def postgres_migrate(args):
    from backend.migrations.configuration import load_migration_settings
    from .postgres_schema import migrate
    values=read_environment_file(Path(args.env_file))
    settings=load_migration_settings(values)
    try:result=migrate(settings)
    except Exception:
        raise PersistenceError('FRAMEWORK_MIGRATION_UNCONFIRMED',
            'Migration failed or its commit was not confirmed. Reconcile the schema before retrying.',outcome='commit_unknown') from None
    print(json.dumps(result));return 0
