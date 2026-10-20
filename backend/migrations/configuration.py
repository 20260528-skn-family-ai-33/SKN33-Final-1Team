"""Owner configuration stays outside the application settings and Engine."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Mapping

from backend.app.config import ConfigurationError, Secret, read_environment_file


@dataclass(frozen=True, slots=True)
class MigrationSettings:
    host: str
    port: int
    database: str
    owner_user: str
    owner_password: Secret
    app_user: str


def load_migration_settings(environ: Mapping[str, str] | None = None) -> MigrationSettings:
    values = dict(os.environ if environ is None else environ)
    if values.get("CC_MIGRATION_ENV_FILE"):
        values = read_environment_file(Path(values["CC_MIGRATION_ENV_FILE"]), values)
    required = ("DB_HOST", "DB_PORT", "POSTGRES_DB", "DB_OWNER_USER", "DB_OWNER_PASSWORD", "DB_APP_USER")
    if any(not values.get(k) or any(c in values[k] for c in "\x00\r\n") for k in required):
        raise ConfigurationError("Explicit DB host/port/name, owner credentials and app role are required for migration.")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", values["DB_HOST"]):
        raise ConfigurationError("Migration DB_HOST must be a hostname or IP, without URL/options.")
    raw_port = values["DB_PORT"]
    if not re.fullmatch(r"[0-9]{1,5}", raw_port) or not 1 <= int(raw_port) <= 65535:
        raise ConfigurationError("Migration DB_PORT must be between 1 and 65535.")
    for key in ("POSTGRES_DB", "DB_OWNER_USER", "DB_APP_USER"):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", values[key]):
            raise ConfigurationError(f"{key} must be an ASCII PostgreSQL identifier of at most 63 bytes.")
    if values["DB_OWNER_USER"] in {values["DB_APP_USER"], "postgres"}:
        raise ConfigurationError("Migration requires a distinct non-admin database owner role.")
    password = values["DB_OWNER_PASSWORD"]
    if not password.strip() or password.startswith("change-me-"):
        raise ConfigurationError("DB_OWNER_PASSWORD must be replaced with a nonblank secret.")
    return MigrationSettings(values["DB_HOST"], int(raw_port), values["POSTGRES_DB"],
                             values["DB_OWNER_USER"], Secret(password), values["DB_APP_USER"])


def create_migration_engine(settings: MigrationSettings):
    from sqlalchemy import URL, create_engine
    from sqlalchemy.pool import NullPool
    return create_engine(
        URL.create("postgresql+psycopg", username=settings.owner_user, password=settings.owner_password.reveal(),
                   host=settings.host, port=settings.port, database=settings.database),
        poolclass=NullPool, hide_parameters=True, echo=False,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC -c client_encoding=UTF8",
                      "application_name": "creative_checker_migration"},
    )


def validate_migration_connection(connection, settings: MigrationSettings):
    from sqlalchemy import text
    from alembic.util import CommandError
    row = connection.execute(text("""
        SELECT current_user, current_database(), d.datdba = o.oid,
               o.rolsuper OR o.rolcreatedb OR o.rolcreaterole OR o.rolreplication OR o.rolbypassrls,
               a.rolsuper OR a.rolcreatedb OR a.rolcreaterole OR a.rolreplication OR a.rolbypassrls,
               EXISTS(SELECT 1 FROM pg_roles r WHERE r.oid <> a.oid AND pg_has_role(a.oid, r.oid, 'MEMBER')),
               current_setting('TimeZone'), current_setting('client_encoding'),
               current_setting('server_version_num')::integer
        FROM pg_database d CROSS JOIN pg_roles o CROSS JOIN pg_roles a
        WHERE d.datname = current_database() AND o.rolname = :owner AND a.rolname = :app
    """), {"owner": settings.owner_user, "app": settings.app_user}).one_or_none()
    if (row is None or row[0] != settings.owner_user or row[1] != settings.database or not row[2]
            or row[3] or row[4] or row[5] or row[6:8] != ("UTC", "UTF8") or not 170000 <= row[8] < 180000):
        raise CommandError("Migration requires the expected PostgreSQL 17 owner, restricted app role without inherited roles, UTC and UTF8.")
