"""Deterministic acknowledgement failures supplement real PostgreSQL tests."""
import traceback
from types import SimpleNamespace

import pytest

from backend.app.config import load_settings
from backend.app.persistence.database import PersistenceError, create_app_engine
from backend.app.persistence.transaction import unit_of_work
from backend.tests.test_db_config import app_environment


@pytest.fixture
def sqlalchemy():
    return pytest.importorskip("sqlalchemy", reason="Optional DB unit tests require requirements-db.txt")


def driver_error(sqlalchemy, state=None):
    from sqlalchemy.exc import OperationalError
    class DriverError(Exception):
        sqlstate = state
    return OperationalError("SQL with secret", {"password": "hidden-password"}, DriverError("hidden-password"))


class FakeConnection:
    def __init__(self, error=None, *, rollback_fails=False, close_fails=False):
        self.error, self.rollback_fails, self.close_fails = error, rollback_fails, close_fails
        self.closed = self.invalidated = self.committed = False
        self.statements = []

    def begin(self):
        return SimpleNamespace(commit=self.commit)

    def exec_driver_sql(self, statement):
        self.statements.append(statement)

    def commit(self):
        if self.error:
            raise self.error
        self.committed = True

    def rollback(self):
        if self.rollback_fails:
            raise RuntimeError("private driver failure")

    def invalidate(self):
        self.invalidated = True

    def close(self):
        self.closed = True
        if self.close_fails:
            raise RuntimeError("private close failure")


@pytest.mark.parametrize("state,code,outcome,retryable", [
    (None, "DB_COMMIT_OUTCOME_UNKNOWN", "commit_unknown", False),
    ("08006", "DB_COMMIT_OUTCOME_UNKNOWN", "commit_unknown", False),
    ("23505", "DB_CONSTRAINT_VIOLATION", "rolled_back", False),
    ("40001", "DB_TRANSACTION_CONFLICT", "rolled_back", True),
    ("40P01", "DB_TRANSACTION_CONFLICT", "rolled_back", True),
    ("57014", "DB_OPERATION_TIMEOUT", "rolled_back", False),
])
def test_commit_error_classification_and_secret_redaction(sqlalchemy, state, code, outcome, retryable):
    connection = FakeConnection(driver_error(sqlalchemy, state))
    with pytest.raises(PersistenceError) as caught:
        with unit_of_work(SimpleNamespace(connect=lambda: connection)):
            pass
    error = caught.value
    assert (error.code, error.outcome, error.retryable) == (code, outcome, retryable)
    assert connection.closed
    assert connection.invalidated == (outcome == "commit_unknown")
    assert "hidden-password" not in "".join(traceback.format_exception(error))
    assert "SQL with secret" not in "".join(traceback.format_exception(error))


def test_unconfirmed_rollback_is_never_retryable(sqlalchemy):
    connection = FakeConnection(driver_error(sqlalchemy, "40001"), rollback_fails=True)
    with pytest.raises(PersistenceError) as caught:
        with unit_of_work(SimpleNamespace(connect=lambda: connection)):
            pass
    assert caught.value.outcome == "rollback_unconfirmed" and not caught.value.retryable
    assert connection.closed


def test_cleanup_failure_retains_successful_commit_outcome(sqlalchemy):
    connection = FakeConnection(close_fails=True)
    with pytest.raises(PersistenceError) as caught:
        with unit_of_work(SimpleNamespace(connect=lambda: connection)):
            pass
    assert connection.committed and caught.value.outcome == "committed"
    assert not caught.value.retryable


def test_rollback_failure_does_not_hide_behind_application_error(sqlalchemy):
    connection = FakeConnection(rollback_fails=True)
    with pytest.raises(PersistenceError) as caught:
        with unit_of_work(SimpleNamespace(connect=lambda: connection)):
            raise ValueError("private application input")
    assert caught.value.code == "DB_ROLLBACK_UNCONFIRMED" and connection.closed
    assert caught.value.outcome == "rollback_unconfirmed" and not caught.value.retryable


def test_process_interruption_during_commit_has_unknown_outcome(sqlalchemy):
    connection = FakeConnection(KeyboardInterrupt())
    with pytest.raises(PersistenceError) as caught:
        with unit_of_work(SimpleNamespace(connect=lambda: connection)):
            pass
    assert caught.value.outcome == "commit_unknown" and connection.invalidated and connection.closed


def test_connection_failure_is_sanitized_and_does_not_enter_uow(sqlalchemy):
    def fail_to_connect():
        raise driver_error(sqlalchemy, "08001")
    with pytest.raises(PersistenceError) as caught:
        with unit_of_work(SimpleNamespace(connect=fail_to_connect)):
            pytest.fail("A failed connection must not yield a transaction or fall back.")
    assert caught.value.code == "DB_CONNECTION_FAILED" and caught.value.outcome == "not_started"
    assert "hidden-password" not in "".join(traceback.format_exception(caught.value))


def test_engine_is_lazy_and_preserves_password_characters(sqlalchemy):
    settings = load_settings(app_environment())
    engine = create_app_engine(settings)
    try:
        assert engine.url.password == settings.database.password.reveal()
        assert settings.database.password.reveal() not in str(engine.url)
        assert settings.database.password.reveal() not in repr(engine)
        assert engine.pool.checkedout() == 0
        assert engine.hide_parameters and engine.pool.size() == 5
    finally:
        engine.dispose()
