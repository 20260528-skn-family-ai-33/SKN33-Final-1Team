"""Owner maintenance exclusion. Table SHARE locks backstop every application writer."""
from contextlib import contextmanager
import threading,time
import portalocker
from sqlalchemy import text
from ..persistence.transaction import unit_of_work
from ..storage.local import StorageError
from .io import OpsError

MAINTENANCE_KEY=374218360617410014


def active(engine):
    with unit_of_work(engine) as c:
        return not c.exec_driver_sql('SELECT pg_try_advisory_xact_lock_shared(%s)',(MAINTENANCE_KEY,)).scalar_one()


@contextmanager
def session_lock(engine,key,*,shared=False):
    c=engine.connect().execution_options(isolation_level='AUTOCOMMIT')
    function='pg_try_advisory_lock_shared' if shared else 'pg_try_advisory_lock'
    unlock='pg_advisory_unlock_shared' if shared else 'pg_advisory_unlock'
    locked=False
    try:
        locked=c.exec_driver_sql('SELECT '+function+'(%s)',(key,)).scalar_one()
        if not locked:raise OpsError('MANAGEMENT_BUSY')
        yield c
    finally:
        if locked:
            try:c.exec_driver_sql('SELECT '+unlock+'(%s)',(key,))
            except Exception:c.invalidate()
        c.close()


@contextmanager
def offline(engine,storage,*,drain_seconds=900):
    if not 0<=drain_seconds<=900:raise OpsError('INVALID_DRAIN_LIMIT')
    deadline=time.monotonic()+drain_seconds
    with session_lock(engine,MAINTENANCE_KEY):
        while True:
            try:storage.start(maintenance=True)
            except (StorageError,portalocker.exceptions.LockException) as error:
                if isinstance(error,StorageError) and error.code!='STORAGE_BUSY':raise
                if time.monotonic()>=deadline:raise OpsError('DRAIN_TIMEOUT') from None
                threading.Event().wait(min(.1,max(0,deadline-time.monotonic())))
            else:break
        with storage.lock('gate'):
            # No live storage service or publication can be running. Reject active
            # inference instead of inventing a terminal state to make a backup pass.
            with unit_of_work(engine) as c:
                if c.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM public.review_runs WHERE execution_status='running') OR EXISTS(SELECT 1 FROM public.review_attempts WHERE execution_status='active')").scalar_one():raise OpsError('ACTIVE_WORK_REQUIRES_RECOVERY')
            yield


@contextmanager
def frozen_database(engine):
    """Separate owner transaction freezes DML while pg_dump reads the whole database."""
    with unit_of_work(engine,import_operation=True) as c:
        names=c.exec_driver_sql("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%' ORDER BY 1,2").all()
        quote=c.dialect.identifier_preparer.quote
        if names:c.exec_driver_sql('LOCK TABLE '+','.join(quote(schema)+'.'+quote(name) for schema,name in names)+' IN SHARE MODE NOWAIT')
        yield c
