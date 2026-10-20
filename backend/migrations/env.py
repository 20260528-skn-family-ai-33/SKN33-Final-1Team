"""Owner-only, single-transaction migration environment. No automatic startup use."""
import re

from alembic import context
from alembic.util import CommandError
from sqlalchemy.exc import SQLAlchemyError

from backend.app.config import ConfigurationError
from backend.app.persistence.schema import metadata
from backend.migrations.configuration import create_migration_engine, load_migration_settings, validate_migration_connection


def run():
    config = context.config
    settings = config.attributes.get("migration_settings") or load_migration_settings()
    config.attributes["migration_settings"] = settings
    if context.is_offline_mode():
        context.configure(dialect_name="postgresql", target_metadata=metadata, literal_binds=True,
                          transactional_ddl=True, transaction_per_migration=False)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_migration_engine(settings)
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '60s'")
                validate_migration_connection(connection, settings)
                connection.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended(current_database() || ':cc-schema-v1', 0))")
                context.configure(connection=connection, target_metadata=metadata, compare_type=True,
                                  transactional_ddl=True, transaction_per_migration=False)
                context.run_migrations()
    except SQLAlchemyError as error:
        # Suppress driver details/parameters; a lost COMMIT must be reconciled via current/inventory.
        state = getattr(getattr(error, "orig", None), "sqlstate", None)
        suffix = f" SQLSTATE={state}." if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}",state) else ""
        raise CommandError("Migration failed or COMMIT was not confirmed. Check current revision and schema before retrying; no fallback was used." + suffix) from None
    finally:
        engine.dispose()


try:
    run()
except ConfigurationError as error:
    raise CommandError(str(error)) from None
