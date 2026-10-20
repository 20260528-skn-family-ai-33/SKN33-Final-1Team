"""A service owns each UoW. Repositories must not commit or keep its Connection."""
from __future__ import annotations

from contextlib import contextmanager
import re
from typing import TYPE_CHECKING

from .database import PersistenceError

if TYPE_CHECKING:
    from sqlalchemy import Engine


def _sqlstate(error: BaseException) -> str | None:
    value = getattr(getattr(error, "orig", None), "sqlstate", None)
    return value if isinstance(value, str) and re.fullmatch(r"[0-9A-Z]{5}", value) else None


def _rollback(connection) -> bool:
    try:
        connection.rollback()
        return not connection.invalidated
    except Exception:
        return False


def _safe_error(error: BaseException, outcome: str) -> PersistenceError:
    state = _sqlstate(error)
    code, message = "DB_OPERATION_FAILED", "The database operation failed."
    if state and state.startswith("23"):
        code, message = "DB_CONSTRAINT_VIOLATION", "A database constraint rejected the operation."
    elif state in {"40001", "40P01"}:
        code, message = "DB_TRANSACTION_CONFLICT", "The database transaction conflicted."
    elif state in {"55P03", "57014"}:
        code, message = "DB_OPERATION_TIMEOUT", "The database operation exceeded its lock or statement limit."
    return PersistenceError(code, message, outcome=outcome, sqlstate=state,
                            retryable=outcome == "rolled_back" and state in {"40001", "40P01"})


@contextmanager
def unit_of_work(engine: Engine, *, import_operation: bool = False):
    """Open, use, commit/rollback and close on one thread. Never automatically retry.

    No network/file/OCR work belongs inside this block. Commit acknowledgements
    can be lost: a subsequent rollback cannot prove that a COMMIT did not succeed.
    """
    from sqlalchemy.exc import SQLAlchemyError

    try:
        connection = engine.connect()
    except PersistenceError:
        raise
    except Exception:
        raise PersistenceError("DB_CONNECTION_FAILED", "Cannot open the configured application database.") from None
    completed = False
    try:
        try:
            transaction = connection.begin()
            connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
            timeout = "60s" if import_operation else "10s"
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = '{timeout}'")
            yield connection
        except BaseException as error:
            rolled_back = _rollback(connection)
            if isinstance(error, SQLAlchemyError):
                raise _safe_error(error, "rolled_back" if rolled_back else "rollback_unconfirmed") from None
            if not rolled_back:
                raise PersistenceError("DB_ROLLBACK_UNCONFIRMED", "The transaction rollback was not confirmed.",
                                       outcome="rollback_unconfirmed") from None
            raise
        try:
            transaction.commit()
        except BaseException as error:
            state = _sqlstate(error)
            # These server rejection responses prove that COMMIT was rejected.
            rejected = bool(state and (state.startswith("23") or state in {"40001", "40P01", "55P03", "57014"}))
            rolled_back = _rollback(connection)
            if rejected:
                raise _safe_error(error, "rolled_back" if rolled_back else "rollback_unconfirmed") from None
            try:
                connection.invalidate()
            except Exception:
                pass
            raise PersistenceError("DB_COMMIT_OUTCOME_UNKNOWN", "COMMIT was not confirmed; reconcile before retrying.",
                                   outcome="commit_unknown", sqlstate=state) from None
        completed = True
    finally:
        try:
            connection.close()
        except Exception:
            try:
                connection.invalidate()
            except Exception:
                pass
            if completed:
                raise PersistenceError("DB_CLEANUP_FAILED", "COMMIT succeeded, but connection cleanup failed.",
                                       outcome="committed") from None
