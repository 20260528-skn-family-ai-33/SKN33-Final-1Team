"""Explicit single-host PostgreSQL worker: python -m backend.app.worker --help.

SIGTERM/SIGINT stop new claims and drain the current bounded local OCR call.
SIGKILL leaves the lease for another worker to recover; no inference is replayed
after dispatch. There is no remote paid-job adapter or automatic submission.
"""
import argparse
from dataclasses import dataclass
from pathlib import Path
import json,os,signal,sys,threading
from .config import ConfigurationError,Settings,load_settings,read_environment_file
from .execution.queue import WorkerQueue,HEARTBEAT_SECONDS
from .security.errors import AuthError


@dataclass(frozen=True,slots=True)
class WorkerSettings:
    persistence: Settings
    environment: str
    storage_root: Path
    @property
    def database(self):return self.persistence.database
    @property
    def persistence_mode(self):return self.persistence.persistence_mode


def worker_profile(values):
    if any(values.get(key) for key in ('DB_OWNER_PASSWORD','POSTGRES_PASSWORD')):
        raise ConfigurationError('Worker requires app-only credentials.')
    persistence=load_settings(values)
    root=Path(values.get('STORAGE_ROOT',''))
    environment=values.get('APP_ENV','production')
    if (persistence.persistence_mode!='postgres' or environment not in ('development','test','production')
        or not root.is_absolute() or '..' in root.parts or values.get('REVIEW_EXECUTOR')!='postgres_worker'
        or values.get('REVIEW_PIPELINE')!='paddle-only' or values.get('RUN_TARGET','local')!='local'
        or values.get('LOCAL_ADMIN_LOGIN','0')!='0'):
        raise ConfigurationError('Explicit postgres_worker, local paddle-only and dedicated storage are required.')
    return WorkerSettings(persistence,environment,root)


def load_worker_settings(values):
    settings=worker_profile(values)
    if settings.environment=='production' and sys.platform!='linux':raise ConfigurationError('Production storage requires Linux.')
    return settings


class AttemptLease:
    """Any heartbeat failure permanently stops this owner's authority, even if DB returns."""
    def __init__(self,queue,claimed,*,interval=HEARTBEAT_SECONDS):
        self.queue=queue;self.run,self.attempt,self.token=claimed;self.interval=interval
        self.stopped=threading.Event();self.lost=threading.Event()
        self.thread=threading.Thread(target=self.renew,name='cc-worker-heartbeat',daemon=True)

    def check(self):
        if self.lost.is_set():raise AuthError('ATTEMPT_AUTHORITY_LOST',409)

    def renew(self):
        while not self.stopped.wait(self.interval):
            try:self.queue.heartbeat(self.run['run_id'],self.attempt,self.token)
            except Exception:self.lost.set();return

    def __enter__(self):self.thread.start();return self
    def __exit__(self,*_):
        self.stopped.set();self.thread.join(timeout=20)
        if self.thread.is_alive():self.lost.set()


def event(name):print(json.dumps({'event':name}),flush=True)


def run_worker(settings,stop,*,once=False,engine=None,adapter=None):
    from .persistence.database import create_app_engine,PersistenceError
    from .persistence.schema_inventory import check_schema
    from .persistence.transaction import unit_of_work
    from .execution.pipeline import PaddlePipeline
    from .services.review_results import ReviewExecutor
    from .services.files import Files
    from .storage.local import LocalStorage
    if adapter is not None and settings.environment!='test':raise ConfigurationError('Injected worker adapters are test-only.')
    owned=engine is None;engine=engine or create_app_engine(settings)
    storage=None
    try:
        check_schema(engine,settings)
        if settings.environment=='production':
            with unit_of_work(engine) as c:
                if c.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM public.users WHERE account_kind='developer' AND is_active)").scalar_one():
                    raise ConfigurationError('Production requires developer accounts to be inactive.')
        storage=LocalStorage(settings.storage_root);storage.start(shared=True)
        queue=WorkerQueue(engine,storage,adapter or PaddlePipeline())
        Files(engine,storage,None).recover(concurrent=True)
        # Reconcile existing result commits only; never run a recovered plan as inference.
        ReviewExecutor(queue,None).recover(concurrent=True)
        event('worker_ready')
        while not stop.is_set():
            try:
                queue.recover_expired()
                if stop.is_set():break
                claimed=queue.claim_next()
                if claimed:
                    with AttemptLease(queue,claimed) as authority:
                        event('worker_claimed')
                        ReviewExecutor(queue,authority).execute_claimed(claimed)
                    if once:break
                    continue
                if once:break
            except (PersistenceError,AuthError):
                # Unconfirmed claim/heartbeat/dispatch is never permission to call OCR.
                event('worker_database_unavailable')
                if once:return 1
            stop.wait(1)
        event('worker_stopped')
        return 0
    finally:
        if storage:storage.close()
        if owned:engine.dispose()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--once',action='store_true',help='Recover expired leases, then process at most one ready matching job.')
    parser.add_argument('--write-config',type=Path,help='Write a NEW admission description from verified local model artifacts; do not start a worker.')
    parser.add_argument('--check-only',action='store_true',help='Validate settings only; no DB/storage/model access.')
    args=parser.parse_args(argv)
    try:
        if args.check_only and args.write_config:raise ConfigurationError('Choose one action.')
        settings=load_worker_settings(read_environment_file(args.env_file))
        if args.check_only:event('worker_configuration_valid');return 0
        if args.write_config:
            from .execution.pipeline import PaddlePipeline
            from .execution.worker_config import write_config
            write_config(args.write_config,PaddlePipeline().describe());event('worker_config_written');return 0
        stop=threading.Event()
        for signum in (signal.SIGTERM,signal.SIGINT):signal.signal(signum,lambda *_:stop.set())
        return run_worker(settings,stop,once=args.once)
    except Exception:
        # A raw driver/model exception must never reach deployment logs.
        event('worker_startup_or_execution_rejected');return 1


if __name__=='__main__':raise SystemExit(main())
