"""C06 metadata and frozen M005/M006 are checked against the saved physical contract."""
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

metadata = metadata_before_relations()
from backend.migrations.configuration import load_migration_settings
from backend.tests.test_migration_config import owner_environment

REVIEW_TABLES = {'review_runs': 40, 'review_attempts': 24, 'item_results': 23, 'run_item_selections': 9}


def test_review_metadata_keys_checks_defaults_and_json_null_policy():
    tables = [metadata.tables['public.' + name] for name in REVIEW_TABLES]
    assert {t.name: len(t.c) for t in tables} == REVIEW_TABLES
    assert sum(len(t.c) for t in tables) == 96
    assert sum(isinstance(c, UniqueConstraint) for t in tables for c in t.constraints) == 9
    assert sum(isinstance(c, CheckConstraint) for t in tables for c in t.constraints) == 13
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
    assert list(metadata.tables['public.run_item_selections'].primary_key.columns.keys()) == ['run_id', 'criteria_item_id']


def test_review_columns_types_nulls_and_default_values_match_design():
    source = Path('docs/DB_Settings/contracts/schema/물리설계_05_검수실행과결과.md').read_text()
    for name in REVIEW_TABLES:
        section = re.search(r'^## \d+\. ' + name + r' — .*?(?=^## |\Z)', source, re.M | re.S)[0]
        rows = re.findall(r'^\| `(\w+)` \| `([^`]+)` \| (필수|선택) \| ([^|]+) \|', section, re.M)
        table = metadata.tables['public.' + name]
        assert [r[0] for r in rows] == list(table.c.keys())
        for column, typename, required, default in rows:
            actual = str(table.c[column].type.compile(dialect=dialect())).replace('TIMESTAMP WITH TIME ZONE', 'TIMESTAMPTZ').replace(', ', ',')
            assert actual == typename and table.c[column].nullable == (required == '선택')
            expected = default.strip().strip('`')
            actual_default = table.c[column].server_default
            if expected in ('없음', 'NULL', '앱 발급'):
                assert actual_default is None
            else:
                assert str(actual_default.arg) == ('CURRENT_TIMESTAMP' if expected == '현재 시각' else expected if expected.isdigit() else "'" + expected + "'")


def test_named_constraint_inventory_matches_design():
    source = Path('docs/DB_Settings/contracts/schema/물리설계_06_제약목록.md').read_text()
    for name in REVIEW_TABLES:
        section = re.search(r'^### ' + name + r'\n.*?(?=^### |^## |\Z)', source, re.M | re.S)[0]
        expected = set(re.findall(r'^\| (?:PK|UNIQUE|CHECK) \| `(\w+)`', section, re.M))
        actual = {c.name for c in metadata.tables['public.' + name].constraints}
        assert actual == expected
        assert all(len(c.encode()) <= 63 for c in actual)


def test_incremental_m005_offline_sql_has_four_tables_without_seed_or_relations():
    output = io.StringIO()
    config = Config(str(Path('backend/migrations/alembic.ini').resolve()), output_buffer=output)
    config.attributes['migration_settings'] = load_migration_settings(owner_environment())
    command.upgrade(config, 'M004:M005', sql=True)
    ddl = output.getvalue()
    assert set(re.findall(r'CREATE TABLE public\.(\w+)', ddl)) == set(REVIEW_TABLES)
    assert ddl.count('CREATE TABLE public.') == 4
    assert 'FOREIGN KEY' not in ddl and 'CREATE INDEX' not in ddl and 'CREATE ROLE' not in ddl
    assert 'INSERT INTO public.' not in ddl and 'owner-@:/?#%$-unit-secret' not in ddl
    assert "UPDATE alembic_version SET version_num='M005'" in ddl
    assert 'BEGIN;' in ddl and 'COMMIT;' in ddl


def test_prior_revisions_are_frozen_and_new_revisions_have_no_live_schema_import():
    proof = json.loads(Path('docs/DB_Settings/verification/reports/2026-10-17-implementation-C04.json').read_text())
    previous = json.loads(Path('docs/DB_Settings/verification/reports/2026-10-17-implementation-C02.json').read_text())
    files = [*previous['runtime_source_files'], *proof['runtime_source_files']]
    c03 = json.loads(Path('docs/DB_Settings/verification/reports/2026-10-17-implementation-C03.json').read_text())
    files.extend(c03['runtime_source_files'])
    c05 = json.loads(Path('docs/DB_Settings/verification/reports/2026-10-17-implementation-C05.json').read_text())
    files.extend(c05['runtime_source_files'])
    for path in ('backend/migrations/versions/M001_identity.py', 'backend/migrations/versions/M002_files.py', 'backend/migrations/versions/M003_ocr.py', 'backend/migrations/versions/M004_criteria.py'):
        expected = next(x['sha256'] for x in files if x['path'] == path)
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
    for filename in ('M005_reviews.py', 'M006_relations.py'):
        tree = ast.parse((Path('backend/migrations/versions') / filename).read_text())
        assert not any(isinstance(n, ast.ImportFrom) and (n.module or '').startswith('backend') for n in ast.walk(tree))


def test_all_81_foreign_keys_match_independent_design_and_policy():
    from backend.app.persistence.schema import metadata as current
    from backend.tests.schema_helpers import foreign_key_contract
    expected = foreign_key_contract()
    actual = []
    assert len(current.tables) == 26 and sum(len(t.c) for t in current.tables.values()) == 395
    assert sum(isinstance(c, UniqueConstraint) for t in current.tables.values() for c in t.constraints) == 41
    assert sum(isinstance(c, CheckConstraint) for t in current.tables.values() for c in t.constraints) == 77
    for table in current.tables.values():
        for constraint in table.foreign_key_constraints:
            assert constraint.ondelete == constraint.onupdate == 'RESTRICT'
            assert constraint.deferrable is False and constraint.match == 'SIMPLE'
            actual.append((table.name, constraint.name, tuple(c.name for c in constraint.columns),
                           constraint.referred_table.name, tuple(e.column.name for e in constraint.elements)))
    assert len(actual) == 81 and sorted(actual) == sorted(expected)
    assert len({c[1] for c in actual}) == 81


def test_m006_offline_sql_adds_only_validated_immediate_restrict_foreign_keys():
    output = io.StringIO()
    config = Config(str(Path('backend/migrations/alembic.ini').resolve()), output_buffer=output)
    config.attributes['migration_settings'] = load_migration_settings(owner_environment())
    command.upgrade(config, 'M005:M006', sql=True)
    ddl = output.getvalue()
    assert ddl.count('ADD CONSTRAINT') == ddl.count('FOREIGN KEY') == 81
    assert ddl.count('ON DELETE RESTRICT ON UPDATE RESTRICT NOT DEFERRABLE') == 81
    assert ddl.count('MATCH SIMPLE') == 81
    assert not any(word in ddl for word in ('CREATE TABLE', 'CREATE INDEX', 'NOT VALID', 'DROP CONSTRAINT', 'CREATE ROLE', 'INSERT INTO public.'))
    assert "version_num='M006'" in ddl and 'BEGIN;' in ddl and 'COMMIT;' in ddl
