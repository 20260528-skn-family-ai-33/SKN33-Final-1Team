"""C04 metadata and frozen M003 are checked against the saved physical contract."""
import ast
import hashlib
import io
import json
from pathlib import Path
import re

import pytest

pytest.importorskip('sqlalchemy', reason='Optional schema tests require requirements-db.txt')
pytest.importorskip('alembic', reason='Optional migration tests require requirements-db.txt')
from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, dialect

from backend.tests.schema_helpers import metadata_before_relations

metadata = metadata_before_relations(['users', 'user_sessions', 'projects', 'project_members', 'assets', 'asset_versions', 'stored_files', 'image_coordinate_frames', 'record_aliases', 'schema_definitions', 'import_packages', 'audit_events', 'ocr_runs', 'ocr_tokens', 'poster_extractions', 'poster_field_observations', 'evidence_records', 'evidence_ocr_tokens'])
from backend.migrations.configuration import load_migration_settings
from backend.tests.test_migration_config import owner_environment

OCR_TABLES = {
    'schema_definitions': 10, 'import_packages': 15, 'audit_events': 10,
    'ocr_runs': 27, 'ocr_tokens': 15, 'poster_extractions': 30,
    'poster_field_observations': 15, 'evidence_records': 19, 'evidence_ocr_tokens': 7,
}


def test_ocr_metadata_keys_checks_defaults_and_json_null_policy():
    tables = [metadata.tables['public.' + name] for name in OCR_TABLES]
    assert {t.name: len(t.c) for t in tables} == OCR_TABLES
    assert sum(len(t.c) for t in tables) == 148
    assert sum(isinstance(c, UniqueConstraint) for t in tables for c in t.constraints) == 14
    assert sum(isinstance(c, CheckConstraint) for t in tables for c in t.constraints) == 28
    assert not any(t.foreign_keys or t.indexes for t in tables)
    for table in tables:
        assert table.primary_key.name == 'pk_' + table.name and table.comment
        assert all(c.comment for c in table.c)
        for column in table.c:
            if isinstance(column.type, JSONB):
                assert column.type.none_as_null is True
            if column.name.endswith('_id'):
                assert column.server_default is None
            if column.server_default is not None and column.name.endswith('_at'):
                assert str(column.server_default.arg) == 'CURRENT_TIMESTAMP'
    assert list(metadata.tables['public.evidence_ocr_tokens'].primary_key.columns.keys()) == ['evidence_id', 'token_id']
    assert metadata.tables['public.ocr_runs'].c.sealed_at.nullable  # deferred seal validation is M007
    assert metadata.tables['public.poster_extractions'].c.sealed_at.nullable


def test_ocr_columns_types_nulls_and_default_values_match_design():
    source = Path('docs/DB_Settings/물리설계_03_규격과OCR근거.md').read_text()
    for name in OCR_TABLES:
        section = re.search(r'^## \d+\. ' + name + r' — .*?(?=^## |\Z)', source, re.M | re.S)[0]
        rows = re.findall(r'^\| `(\w+)` \| `([^`]+)` \| (필수|선택) \| ([^|]+) \|', section, re.M)
        table = metadata.tables['public.' + name]
        assert [r[0] for r in rows] == list(table.c.keys())
        for column, typename, required, default in rows:
            actual = str(table.c[column].type.compile(dialect=dialect())).replace('TIMESTAMP WITH TIME ZONE', 'TIMESTAMPTZ')
            assert actual == typename and table.c[column].nullable == (required == '선택')
            expected = default.strip().strip('`')
            actual_default = table.c[column].server_default
            if expected in ('없음', 'NULL', '앱 발급'):
                assert actual_default is None
            else:
                assert str(actual_default.arg) == ('CURRENT_TIMESTAMP' if expected == '현재 시각' else "'" + expected + "'")


def test_named_constraint_inventory_matches_design():
    source = Path('docs/DB_Settings/물리설계_06_제약목록.md').read_text()
    for name in OCR_TABLES:
        section = re.search(r'^### ' + name + r'\n.*?(?=^### |^## |\Z)', source, re.M | re.S)[0]
        expected = set(re.findall(r'^\| (?:PK|UNIQUE|CHECK) \| `(\w+)`', section, re.M))
        actual = {c.name for c in metadata.tables['public.' + name].constraints}
        assert actual == expected
        assert all(len(c.encode()) <= 63 for c in actual)


def test_incremental_m003_offline_sql_has_nine_tables_without_seed_or_relations():
    output = io.StringIO()
    config = Config(str(Path('backend/migrations/alembic.ini').resolve()), output_buffer=output)
    config.attributes['migration_settings'] = load_migration_settings(owner_environment())
    command.upgrade(config, 'M002:M003', sql=True)
    ddl = output.getvalue()
    assert set(re.findall(r'CREATE TABLE public\.(\w+)', ddl)) == set(OCR_TABLES)
    assert ddl.count('CREATE TABLE public.') == 9
    assert 'FOREIGN KEY' not in ddl and 'CREATE INDEX' not in ddl and 'CREATE ROLE' not in ddl
    assert 'INSERT INTO public.' not in ddl and 'owner-@:/?#%$-unit-secret' not in ddl
    assert "UPDATE alembic_version SET version_num='M003'" in ddl
    assert 'BEGIN;' in ddl and 'COMMIT;' in ddl


def test_prior_revisions_are_frozen_and_m003_has_no_live_schema_import():
    proof = json.loads(Path('docs/DB_Settings/verification/2026-10-17-implementation-C03.json').read_text())
    old_proof = json.loads(Path('docs/DB_Settings/verification/2026-10-17-implementation-C02.json').read_text())
    for path, record in [('backend/migrations/versions/M001_identity.py', old_proof),
                         ('backend/migrations/versions/M002_files.py', proof)]:
        expected = next(x['sha256'] for x in record['runtime_source_files'] if x['path'] == path)
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
    tree = ast.parse(Path('backend/migrations/versions/M003_ocr.py').read_text())
    assert not any(isinstance(n, ast.ImportFrom) and (n.module or '').startswith('backend') for n in ast.walk(tree))
