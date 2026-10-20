"""C07a indexes are reviewable while preserving the historical draft after M007 promotion."""
import ast
import hashlib
import io
import json
from pathlib import Path

import pytest

pytest.importorskip('sqlalchemy',reason='Optional index tests require requirements-db.txt')
pytest.importorskip('alembic',reason='Optional draft tests require requirements-db.txt')
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy.sql import operators

from backend.tests.index_fixtures import DRAFT, index_contract, preview_metadata, stage_index_draft
from backend.tests.test_migration_config import owner_environment
from backend.migrations.configuration import load_migration_settings


def config_for(directory=None, *, enabled=False, output=None):
    config=Config(str(Path('backend/migrations/alembic.ini').resolve()),output_buffer=output)
    config.attributes['migration_settings']=load_migration_settings(owner_environment())
    if directory:
        config.set_main_option('script_location',str(directory))
    if enabled:
        config.attributes['allow_c07a_index_draft']=True
    return config


def test_all_27_index_definitions_match_independent_contract_and_do_not_touch_head():
    from backend.app.persistence.schema import metadata
    before={t.name:len(t.indexes) for t in metadata.tables.values()}
    preview=preview_metadata()
    actual=[]
    for table in preview.tables.values():
        for index in table.indexes:
            keys=[]
            for expr in index.expressions:
                desc=getattr(expr,'modifier',None) is operators.desc_op
                keys.append((expr.element.name if desc else expr.name,desc))
            where=index.dialect_options['postgresql']['where']
            actual.append((index.name,table.name,tuple(keys),index.unique,None if where is None else str(where)))
    assert sorted(actual)==sorted(index_contract())
    assert len(actual)==27 and sum(s[3] for s in actual)==2 and sum(s[4] is not None for s in actual)==13
    assert all(len(s[0].encode())<=63 for s in actual)
    assert {t.name:len(t.indexes) for t in metadata.tables.values()}==before
    assert sum(before.values())==27


def test_no_exact_duplicate_of_pk_or_unique_supporting_indexes():
    from sqlalchemy import UniqueConstraint
    preview=preview_metadata()
    for table in preview.tables.values():
        supports={tuple(c.name for c in table.primary_key.columns)}
        supports.update(tuple(c.name for c in uq.columns) for uq in table.constraints if isinstance(uq,UniqueConstraint))
        for index in table.indexes:
            keys=tuple(getattr(expr,'element',expr).name for expr in index.expressions)
            assert keys not in supports
    for name,_,_,_,predicate in index_contract():
        if predicate:
            assert not any(word in predicate.lower() for word in ('now(', 'current_timestamp', 'clock_timestamp', ':cutoff'))


def test_real_head_is_m007_and_m001_through_m006_are_unchanged():
    assert ScriptDirectory.from_config(config_for()).get_heads()==['M008']
    assert list(Path('backend/migrations/versions').glob('M007*'))
    fingerprints={}
    for unit in ('C02','C03','C04','C05','C06'):
        proof=json.loads(Path('docs/DB_Settings/verification/reports/2026-10-17-implementation-'+unit+'.json').read_text())
        fingerprints.update({x['path']:x['sha256'] for x in proof['runtime_source_files']})
    for path in Path('backend/migrations/versions').glob('*.py'):
        if path.name.startswith(('M007','M008')):continue
        assert hashlib.sha256(path.read_bytes()).hexdigest()==fingerprints[str(path)]
    tree=ast.parse(DRAFT.read_text())
    assert not any(isinstance(n,ast.ImportFrom) and (n.module or '').startswith('backend') for n in ast.walk(tree))


def test_incomplete_draft_refuses_normal_execution(tmp_path):
    directory=stage_index_draft(tmp_path)
    with pytest.raises(CommandError,match='Incomplete M007 index draft'):
        command.upgrade(config_for(directory,output=io.StringIO()),'M006:M007',sql=True)


def test_staged_offline_sql_only_creates_reviewed_indexes(tmp_path):
    directory=stage_index_draft(tmp_path);output=io.StringIO()
    command.upgrade(config_for(directory,enabled=True,output=output),'M006:M007',sql=True)
    ddl=output.getvalue()
    assert ddl.count('CREATE INDEX ')==25 and ddl.count('CREATE UNIQUE INDEX ')==2
    assert all(name in ddl for name,_,_,_,_ in index_contract())
    assert ' DESC' in ddl and ' WHERE ' in ddl
    assert all(word not in ddl for word in ('CREATE TABLE','CREATE FUNCTION','CREATE TRIGGER','CREATE ROLE','GRANT ','CONCURRENTLY','IF NOT EXISTS'))
    assert 'owner-@:/?#%$-unit-secret' not in ddl
    assert 'BEGIN;' in ddl and 'COMMIT;' in ddl
