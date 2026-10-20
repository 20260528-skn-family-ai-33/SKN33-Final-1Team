"""One BackgroundTasks owner per database, including different storage roots.

The autocommit session is separate from the request pool and owns no transaction.
A lost session invalidates this process's authority; it is never auto reacquired.
"""
import threading
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from ..security.errors import AuthError

SERVICE_KEY=374218360617410001


class Leader:
    def __init__(self,engine):
        self.engine=create_engine(engine.url,poolclass=NullPool,isolation_level='AUTOCOMMIT',connect_args={'connect_timeout':5})
        self.connection=None;self.pid=None;self.lock=threading.Lock();self.lost=False
        try:
            self.connection=self.engine.connect()
            if not self.connection.exec_driver_sql('SELECT pg_try_advisory_lock(%s)',(SERVICE_KEY,)).scalar_one():
                raise AuthError('BACKGROUND_SERVICE_BUSY',503)
            self.pid=self.connection.exec_driver_sql('SELECT pg_backend_pid()').scalar_one()
        except BaseException:self.close();raise

    def check(self):
        with self.lock:
            if self.lost:raise AuthError('BACKGROUND_AUTHORITY_LOST',503)
            try:
                if self.connection.exec_driver_sql('SELECT pg_backend_pid()').scalar_one()!=self.pid:raise ValueError()
            except Exception:
                self.lost=True
                raise AuthError('BACKGROUND_AUTHORITY_LOST',503) from None

    def close(self):
        if self.connection is not None:self.connection.close();self.connection=None
        self.engine.dispose()
