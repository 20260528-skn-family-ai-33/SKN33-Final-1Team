"""Explicit app/owner separation and framework-only process startup."""
import json
from pathlib import Path
import signal
import sys
from threading import Event

from backend.app.config import load_settings,read_environment_file,ConfigurationError
from backend.app.persistence.database import create_app_engine,PersistenceError
from backend.app.persistence.schema_inventory import check_schema
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from .composition import load_config,build_agents
from .postgres import PostgresStore
from .postgres_schema import check_extension,check_app_permissions
from backend.runtime_environment import activate_runtime


def _values(filename, component='v1'):
    # An explicit file is required; never discover or modify the user's .env.
    values=activate_runtime(component, Path(filename))
    if any(values.get(k) for k in ('DB_OWNER_PASSWORD','POSTGRES_PASSWORD')):
        raise ConfigurationError('Runtime requires app-only credentials.')
    if values.get('APP_ENV') not in ('development','test','production'):
        raise ConfigurationError('Select APP_ENV=development, test or production explicitly.')
    settings=load_settings(values)
    if settings.persistence_mode!='postgres':raise ConfigurationError('Select PostgreSQL explicitly.')
    if values.get('APP_ENV')=='production':
        from .deployment import require_verified_database
        require_verified_database(settings)
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
    from .deployment import trusted_proxies
    proxies=trusted_proxies(values.get('TRUSTED_PROXY_IPS',''))
    root=Path(args.pdf_storage_root).expanduser().resolve() if args.pdf_storage_root else None
    posters=Path(args.poster_storage_root).expanduser().resolve() if args.poster_storage_root else None
    app=create_postgres_app(load_v1_settings(values),load_config(args.config),pdf_storage_root=root,poster_storage_root=posters)
    uvicorn.run(app,host=getattr(args,'host','127.0.0.1'),port=args.port,workers=1,reload=False,
                proxy_headers=bool(proxies),forwarded_allow_ips=proxies)
    return 0


def postgres_worker(args):
    from .durable_worker import PostgresQueue,execute_claim
    values,settings=_values(args.env_file, 'framework-worker')
    configuration=load_config(args.config)
    if values.get('APP_ENV')=='production':
        from .deployment import require_live_configuration
        require_live_configuration(configuration)
    engine=create_app_engine(settings)
    stop=Event()
    storage=None
    poster_storage=None
    for signum in (signal.SIGTERM,signal.SIGINT):signal.signal(signum,lambda *_:stop.set())
    try:
        _ready(engine,settings)
        if configuration.context_preparation is not None:
            with unit_of_work(engine) as c:
                if check_extension(c)['revision']!='AF005':raise ConfigurationError('Neo4j evidence preparation requires AF005.')
        store=PostgresStore(engine,configuration)
        queue=PostgresQueue(store)
        if configuration.pdf_extraction is not None:
            from .documents import open_document_storage
            from .postgres_schema import check_document_extension
            from .a02 import build_postgres_agents
            if not args.pdf_storage_root:raise ConfigurationError('Registered PDF extraction requires a dedicated PDF storage root.')
            with unit_of_work(engine) as c:check_document_extension(c)
            storage=open_document_storage(Path(args.pdf_storage_root).expanduser().resolve())
            if configuration.roles['A04_A05'].kind=='registered_poster':
                from .poster_review import open_poster_storage
                if not args.poster_storage_root:raise ConfigurationError('Registered poster inspection requires the existing image storage root.')
                poster_storage=open_poster_storage(Path(args.poster_storage_root).expanduser().resolve())
            agents=build_postgres_agents(configuration,store,storage,api_key=values.get('A02_QWEN_API_KEY'),poster_storage=poster_storage)
            if configuration.pdf_extraction.mode=='mock':print('SYNTHETIC A02 MOCK: manual-only fixture candidates, no Qwen call.',file=sys.stderr)
        else:agents=build_agents(configuration)  # Only worker initializes role factories.
        print(json.dumps({'event':'framework_worker_ready','pdf_extraction_mode':configuration.pdf_extraction.mode if configuration.pdf_extraction else None}),flush=True)
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
    finally:
        if poster_storage is not None:poster_storage.close()
        if storage is not None:storage.close()
        engine.dispose()


def postgres_migrate(args):
    from backend.migrations.configuration import load_migration_settings
    from .postgres_schema import migrate
    values=read_environment_file(Path(args.env_file))
    settings=load_migration_settings(values)
    try:result=migrate(settings,target=args.revision)
    except Exception:
        raise PersistenceError('FRAMEWORK_MIGRATION_UNCONFIRMED',
            'Migration failed or its commit was not confirmed. Reconcile the schema before retrying.',outcome='commit_unknown') from None
    print(json.dumps(result));return 0


def initialize_pdf_storage(args):
    from backend.app.storage.local import StorageError
    from .documents import initialize_document_storage
    from .ports import AdapterFailure
    try:initialize_document_storage(Path(args.root).expanduser().resolve())
    except (StorageError,OSError):
        raise AdapterFailure('PDF_STORAGE_INITIALIZATION_FAILED',
            'Use a new dedicated local PDF storage directory on a supported filesystem.') from None
    print(json.dumps({'storage':'framework_pdf','initialized':True}));return 0
