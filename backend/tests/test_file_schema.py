"""C03 metadata and incremental SQL match the saved physical contract."""
import ast
import hashlib
import io
import json
from pathlib import Path
import re

import pytest

pytest.importorskip("sqlalchemy", reason="Optional schema tests require requirements-db.txt")
pytest.importorskip("alembic", reason="Optional migration tests require requirements-db.txt")
from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import dialect

from backend.tests.schema_helpers import metadata_before_relations

metadata = metadata_before_relations(['users', 'user_sessions', 'projects', 'project_members', 'assets', 'asset_versions', 'stored_files', 'image_coordinate_frames', 'record_aliases'])
from backend.migrations.configuration import load_migration_settings
from backend.tests.test_migration_config import owner_environment

FILE_TABLES = {"assets":8,"asset_versions":15,"stored_files":16,"image_coordinate_frames":12,"record_aliases":7}


def test_file_metadata_counts_and_unique_null_policy():
    tables = [metadata.tables["public."+name] for name in FILE_TABLES]
    assert {t.name:len(t.c) for t in tables} == FILE_TABLES
    prior = [t for t in metadata.tables.values() if t.name in
             {"users", "user_sessions", "projects", "project_members", *FILE_TABLES}]
    assert len(prior)==9 and sum(len(t.c) for t in prior)==88
    assert sum(isinstance(c,UniqueConstraint) for t in tables for c in t.constraints)==7
    assert sum(isinstance(c,CheckConstraint) for t in tables for c in t.constraints)==15
    assert not any(t.foreign_keys or t.indexes for t in tables)
    aliases=metadata.tables["public.record_aliases"]
    unique=next(c for c in aliases.constraints if isinstance(c,UniqueConstraint))
    assert unique.dialect_options["postgresql"]["nulls_not_distinct"] is True
    assert metadata.tables["public.image_coordinate_frames"].c.transform_jsonb.type.none_as_null is True


def test_columns_types_and_null_flags_match_saved_design():
    source=Path("docs/DB_Settings/물리설계_02_사용자와파일.md").read_text()
    for name in FILE_TABLES:
        section=re.search(r"^## \d+\. "+name+r" — .*?(?=^## |\Z)",source,re.M|re.S)[0]
        rows=re.findall(r"^\| `(\w+)` \| `([^`]+)` \| (필수|선택) \|",section,re.M)
        table=metadata.tables["public."+name]
        assert [r[0] for r in rows]==list(table.c.keys())
        for column,typename,required in rows:
            actual=str(table.c[column].type.compile(dialect=dialect())).replace("TIMESTAMP WITH TIME ZONE","TIMESTAMPTZ")
            assert actual==typename and table.c[column].nullable==(required=="선택")
            assert table.c[column].comment


def test_incremental_offline_sql_only_creates_five_tables():
    output=io.StringIO();config=Config(str(Path("backend/migrations/alembic.ini").resolve()),output_buffer=output)
    config.attributes["migration_settings"]=load_migration_settings(owner_environment())
    command.upgrade(config,"M001:M002",sql=True)
    ddl=output.getvalue()
    assert ddl.count("CREATE TABLE public.")==5
    assert "UNIQUE NULLS NOT DISTINCT" in ddl and "CREATE TABLE public.users" not in ddl
    assert "FOREIGN KEY" not in ddl and "CREATE INDEX" not in ddl and "CREATE ROLE" not in ddl
    assert "INSERT INTO public." not in ddl and "owner-@:/?#%$-unit-secret" not in ddl
    assert "UPDATE alembic_version SET version_num='M002'" in ddl


def test_existing_revision_is_unchanged_and_new_revision_has_no_live_schema_import():
    proof=json.loads(Path("docs/DB_Settings/verification/2026-10-17-implementation-C02.json").read_text())
    old=Path("backend/migrations/versions/M001_identity.py")
    expected=next(x["sha256"] for x in proof["runtime_source_files"] if x["path"]==str(old))
    assert hashlib.sha256(old.read_bytes()).hexdigest()==expected
    tree=ast.parse(Path("backend/migrations/versions/M002_files.py").read_text())
    assert not any(isinstance(n,ast.ImportFrom) and (n.module or "").startswith("backend") for n in ast.walk(tree))
