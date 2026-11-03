"""Real PostgreSQL only. Explicit opt-in creates and drops ONE new test database."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import os
from pathlib import Path
import threading
from uuid import uuid4

import pytest

from backend.app.config import load_settings, read_environment_file
from backend.app.persistence.database import PersistenceError, check_connection, create_app_engine
from backend.app.persistence.transaction import unit_of_work

pytestmark = pytest.mark.db_integration


@dataclass
class TestDatabase:
    __test__ = False
    engine: object
    settings: object
    name: str
    environment: object = field(repr=False)


@pytest.fixture(scope="module")
def database():
    # An explicit test-only file is allowed; there is no .env or application DB fallback.
    env = (read_environment_file(Path(os.environ["TEST_DB_ENV_FILE"]))
           if os.environ.get("TEST_DB_ENV_FILE") else os.environ)
    if env.get("TEST_DB_ALLOW_CREATE") != "1":
        pytest.fail("Selected db_integration requires TEST_DB_ALLOW_CREATE=1 and explicit TEST_DB_* credentials.")
    names = ["TEST_DB_HOST", "TEST_DB_PORT", "TEST_DB_ADMIN_USER", "TEST_DB_ADMIN_PASSWORD",
             "TEST_DB_OWNER_USER", "TEST_DB_OWNER_PASSWORD", "TEST_DB_APP_USER", "TEST_DB_APP_PASSWORD"]
    if any(not env.get(name) for name in names):
        pytest.fail("Selected db_integration is missing explicit TEST_DB_* connection settings; no fallback is allowed.")
    try:
        import psycopg
        from psycopg import sql
        import sqlalchemy  # noqa: F401
    except ImportError:
        pytest.fail("Selected db_integration requires requirements-db.txt; missing packages are not skipped.")
    name = "creative_checker_test_" + uuid4().hex
    assert name.startswith("creative_checker_test_") and len(name) <= 63
    common = {"host": env["TEST_DB_HOST"], "port": int(env["TEST_DB_PORT"]), "connect_timeout": 5}
    # Only this generated DB can be created/dropped; an env-supplied business DB name is never used.
    created, engine, admin = False, None, None
    try:
        admin = psycopg.connect(**common, dbname="postgres", user=env["TEST_DB_ADMIN_USER"],
                                password=env["TEST_DB_ADMIN_PASSWORD"], autocommit=True)
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(env["TEST_DB_OWNER_USER"])))
        created = True
        with psycopg.connect(**common, dbname=name, user=env["TEST_DB_OWNER_USER"], password=env["TEST_DB_OWNER_PASSWORD"]) as owner:
            owner.execute("CREATE SCHEMA c01_probe")
            owner.execute("REVOKE ALL ON SCHEMA c01_probe FROM PUBLIC")
            owner.execute(sql.SQL("GRANT USAGE ON SCHEMA c01_probe TO {}").format(sql.Identifier(env["TEST_DB_APP_USER"])))
            owner.execute("CREATE TABLE c01_probe.records (id TEXT PRIMARY KEY, value TEXT UNIQUE DEFERRABLE INITIALLY DEFERRED)")
            owner.execute(sql.SQL("GRANT SELECT, INSERT ON c01_probe.records TO {}").format(sql.Identifier(env["TEST_DB_APP_USER"])))
        settings = load_settings({"PERSISTENCE_MODE": "postgres", "DB_HOST": common["host"],
                                  "DB_PORT": str(common["port"]), "POSTGRES_DB": name,
                                  "DB_OWNER_USER": env["TEST_DB_OWNER_USER"], "DB_APP_USER": env["TEST_DB_APP_USER"],
                                  "DB_APP_PASSWORD": env["TEST_DB_APP_PASSWORD"]})
        engine = create_app_engine(settings)
        yield TestDatabase(engine, settings, name, env)
    finally:
        if engine is not None:
            engine.dispose()
        if admin is not None:
            try:
                if created:
                    # No FORCE or unrelated session termination; all app connections must already be closed.
                    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            finally:
                admin.close()


def insert(connection, key, value=None):
    connection.exec_driver_sql("INSERT INTO c01_probe.records (id, value) VALUES (%s, %s)", (key, value or key))


def exists(database, key):
    with unit_of_work(database.engine) as connection:
        return connection.exec_driver_sql("SELECT count(*) FROM c01_probe.records WHERE id = %s", (key,)).scalar_one()


def test_connection_identity_limits_and_release(database):
    assert database.environment["TEST_DB_APP_PASSWORD"] not in repr(database)
    assert database.environment["TEST_DB_OWNER_PASSWORD"] not in repr(database)
    assert database.environment["TEST_DB_ADMIN_PASSWORD"] not in repr(database)
    result = check_connection(database.engine, database.settings)
    assert result["current_user"] == database.settings.database.username
    assert result["database"] == database.name
    assert result["timezone"] == "UTC" and result["client_encoding"] == "UTF8"
    assert 170000 <= result["server_version_num"] < 180000
    with unit_of_work(database.engine) as connection:
        assert connection.exec_driver_sql("SHOW transaction_isolation").scalar_one() == "read committed"
        assert connection.exec_driver_sql("SHOW lock_timeout").scalar_one() == "2s"
        assert connection.exec_driver_sql("SHOW statement_timeout").scalar_one() == "10s"
    assert connection.closed and database.engine.pool.checkedout() == 0


def test_commit_persists_to_an_independent_connection(database):
    key = uuid4().hex
    with unit_of_work(database.engine) as connection:
        insert(connection, key)
    assert exists(database, key) == 1


def test_application_exception_rolls_back(database):
    key = uuid4().hex
    with pytest.raises(ValueError, match="deliberate application failure"):
        with unit_of_work(database.engine) as connection:
            insert(connection, key)
            raise ValueError("deliberate application failure")
    assert connection.closed and exists(database, key) == 0


def test_sql_failure_rolls_back_and_pool_recovers(database):
    key = uuid4().hex
    with pytest.raises(PersistenceError) as caught:
        with unit_of_work(database.engine) as connection:
            insert(connection, key)
            connection.exec_driver_sql("SELECT 1 / 0")
    assert caught.value.sqlstate == "22012" and caught.value.outcome == "rolled_back"
    assert exists(database, key) == 0
    assert check_connection(database.engine, database.settings)["status"] == "connected"


def test_deferred_commit_rejection_is_not_success_or_unknown(database):
    value = uuid4().hex
    with pytest.raises(PersistenceError) as caught:
        with unit_of_work(database.engine) as connection:
            insert(connection, uuid4().hex, value)
            insert(connection, uuid4().hex, value)
            # The deferred UNIQUE permits both statements; only COMMIT fails.
    assert caught.value.code == "DB_CONSTRAINT_VIOLATION" and caught.value.sqlstate == "23505"
    assert caught.value.outcome == "rolled_back" and not caught.value.retryable
    with unit_of_work(database.engine) as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM c01_probe.records WHERE value = %s", (value,)).scalar_one() == 0


def test_statement_cancellation_releases_connection(database):
    with pytest.raises(PersistenceError) as caught:
        with unit_of_work(database.engine) as connection:
            connection.exec_driver_sql("SET LOCAL statement_timeout = '20ms'")
            connection.exec_driver_sql("SELECT pg_sleep(1)")
    assert caught.value.code == "DB_OPERATION_TIMEOUT" and caught.value.outcome == "rolled_back"
    assert check_connection(database.engine, database.settings)["status"] == "connected"


def test_threads_have_independent_live_connections(database):
    barrier = threading.Barrier(2)
    def transaction(_):
        with unit_of_work(database.engine) as connection:
            pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            barrier.wait(timeout=10)
            return pid
    with ThreadPoolExecutor(max_workers=2) as workers:
        pids = list(workers.map(transaction, range(2)))
    assert len(set(pids)) == 2 and database.engine.pool.checkedout() == 0


def test_privileged_role_is_rejected_even_if_configuration_omits_owner(database):
    # Deliberately omit the real owner name: the server-side guard must still reject its privileges.
    env = database.environment
    settings = load_settings({"PERSISTENCE_MODE": "postgres", "DB_HOST": env["TEST_DB_HOST"],
                              "DB_PORT": env["TEST_DB_PORT"], "POSTGRES_DB": database.name,
                              "DB_OWNER_USER": "different_owner_name", "DB_APP_USER": env["TEST_DB_OWNER_USER"],
                              "DB_APP_PASSWORD": env["TEST_DB_OWNER_PASSWORD"]})
    engine = create_app_engine(settings)
    try:
        with pytest.raises(PersistenceError) as caught:
            check_connection(engine, settings)
        assert caught.value.code == "DB_ROLE_REJECTED"
    finally:
        engine.dispose()
