"""Lazy Engine construction and read-only connection/role diagnostics."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import Settings

if TYPE_CHECKING:
    from sqlalchemy import Engine


class PersistenceError(RuntimeError):
    """Safe diagnostic: never contains driver messages, SQL, parameters or credentials."""

    def __init__(self, code: str, message: str, *, outcome: str = "not_started",
                 sqlstate: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.outcome = outcome
        self.sqlstate = sqlstate
        self.retryable = retryable


# Check effective role membership as well as role attributes. No owner credentials are needed.
_IDENTITY_SQL = """
SELECT current_user, current_database(), current_setting('TimeZone'),
       current_setting('server_version_num')::integer, current_setting('client_encoding'),
       EXISTS (
           SELECT 1 FROM pg_roles r
           WHERE pg_has_role(current_user, r.oid, 'MEMBER')
             AND (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
                  OR r.rolbypassrls OR r.oid = d.datdba
                  OR r.rolname IN ('pg_write_all_data', 'pg_read_all_data',
                     'pg_execute_server_program', 'pg_read_server_files', 'pg_write_server_files'))
       )
FROM pg_database d WHERE d.datname = current_database()
"""


def _validate_identity(row, settings):
    if not row or row[0] != settings.username or row[1] != settings.database or row[5]:
        raise PersistenceError("DB_ROLE_REJECTED", "The connection is not using the expected restricted app role.")
    if row[2] != "UTC" or row[4] != "UTF8":
        raise PersistenceError("DB_CONFIGURATION_INVALID", "The connection must use UTC and UTF8.")


def create_app_engine(settings: Settings) -> Engine:
    """Call once per process and dispose on shutdown; construction itself does not connect."""
    db = settings.database
    if settings.persistence_mode != "postgres" or db is None:
        raise PersistenceError("PERSISTENCE_DISABLED", "PostgreSQL persistence is not enabled.")
    try:
        from sqlalchemy import URL, create_engine, event
        import psycopg  # noqa: F401 -- fail clearly before a first connection
    except ImportError:
        raise PersistenceError("DB_DEPENDENCIES_MISSING", "Install requirements-db.txt for PostgreSQL support.") from None
    url = URL.create("postgresql+psycopg", username=db.username, password=db.password.reveal(),
                     host=db.host, port=db.port, database=db.database)
    engine = create_engine(
        url, isolation_level="READ COMMITTED", pool_size=db.pool_size, max_overflow=0,
        pool_timeout=db.pool_timeout, pool_pre_ping=True, hide_parameters=True,
        echo=False, echo_pool=False,
        connect_args={"connect_timeout": db.connect_timeout,
                      "options": "-c timezone=UTC -c client_encoding=UTF8",
                      "application_name": "creative_checker_app"},
    )

    @event.listens_for(engine, "connect")
    def verify_app_connection(connection, _record):
        try:
            with connection.cursor() as cursor:
                cursor.execute(_IDENTITY_SQL)
                _validate_identity(cursor.fetchone(), db)
        finally:
            # The identity SELECT must not leave a transaction open in the pool.
            connection.rollback()

    return engine


def check_connection(engine: Engine, settings: Settings) -> dict[str, object]:
    """SELECT only; the UoW closes the connection before the safe result is returned."""
    from .transaction import unit_of_work

    with unit_of_work(engine) as connection:
        assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
        row = connection.exec_driver_sql(_IDENTITY_SQL).one()
        _validate_identity(row, settings.database)
        result = {"status": "connected", "database": row[1], "current_user": row[0],
                  "timezone": row[2], "server_version_num": row[3], "client_encoding": row[4]}
    return result
