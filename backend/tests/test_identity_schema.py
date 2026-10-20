"""DDL/metadata review is offline and distinct from PostgreSQL behavior tests."""
import io
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy", reason="Optional schema tests require requirements-db.txt")
pytest.importorskip("alembic", reason="Optional migration tests require requirements-db.txt")
from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, UniqueConstraint

from backend.tests.schema_helpers import metadata_before_relations

metadata = metadata_before_relations(['users', 'user_sessions', 'projects', 'project_members'])
from backend.migrations.configuration import load_migration_settings
from backend.tests.test_migration_config import owner_environment


def test_identity_metadata_matches_design_without_future_fk_or_indexes():
    tables = [metadata.tables["public." + name] for name in ("users", "user_sessions", "projects", "project_members")]
    assert {t.name:len(t.columns) for t in tables} == {"users":8,"user_sessions":6,"projects":10,"project_members":6}
    assert sum(isinstance(c, UniqueConstraint) for t in tables for c in t.constraints) == 2
    assert sum(isinstance(c, CheckConstraint) for t in tables for c in t.constraints) == 10
    assert not any(t.foreign_keys or t.indexes for t in tables)
    for table in tables:
        assert table.primary_key.name == "pk_" + table.name and table.comment
        assert all(c.comment for c in table.columns)
    assert metadata.tables["public.users"].c.email.nullable
    assert metadata.tables["public.users"].c.password_hash.nullable
    assert metadata.tables["public.users"].c.user_id.server_default is None


def test_offline_revision_is_reviewable_and_contains_no_credentials_or_seed(monkeypatch):
    monkeypatch.delenv("CC_MIGRATION_ENV_FILE", raising=False)
    output = io.StringIO()
    config = Config(str(Path("backend/migrations/alembic.ini").resolve()), output_buffer=output)
    config.attributes["migration_settings"] = load_migration_settings(owner_environment())
    command.upgrade(config, "M001", sql=True)
    ddl = output.getvalue()
    assert "owner-@:/?#%$-unit-secret" not in ddl and "postgresql+psycopg" not in ddl
    assert ddl.count("CREATE TABLE public.") == 4
    assert ddl.index("ALTER DEFAULT PRIVILEGES") < ddl.index("CREATE TABLE public.users")
    assert "REVOKE ALL ON TABLE public.alembic_version" in ddl
    assert "FOREIGN KEY" not in ddl and "CREATE INDEX" not in ddl
    assert "CREATE ROLE" not in ddl and "INSERT INTO public.users" not in ddl
    assert "BEGIN;" in ddl and "COMMIT;" in ddl
