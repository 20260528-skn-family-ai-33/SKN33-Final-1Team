"""Final M008 policy and catalog contract are frozen independently of live metadata."""
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest
pytest.importorskip('sqlalchemy');pytest.importorskip('alembic')
from alembic import command
from alembic.script import ScriptDirectory
from backend.tests.grant_fixtures import expected_privileges,helper_functions,update_policy
from backend.tests.test_index_schema import config_for

CONTRACT=Path('backend/app/persistence/schema_contract.json')


def test_m008_policy_matches_independent_design_and_contract():
    spec=importlib.util.spec_from_file_location('c07c_frozen','backend/migrations/versions/M008_grants.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    contract=json.loads(CONTRACT.read_text());structure=contract['structure']
    assert migration.revision=='M008' and migration.down_revision=='M007'
    assert migration.UPDATE_COLUMNS==update_policy()
    assert {s.split('(')[0] for s in migration.EXECUTE_FUNCTIONS}==helper_functions()
    assert len(migration.ALL_FUNCTIONS)==19
    assert contract['privileges']==expected_privileges(structure)
    assert len(structure['relations'])==27 and len(structure['columns'])==396
    assert len(structure['indexes'])==95 and len(structure['triggers'])==35 and len(structure['functions'])==19
    from collections import Counter
    assert Counter(r[2] for r in structure['constraints'])=={'p':27,'u':41,'c':77,'f':81,'t':18}
    from backend.app.persistence.schema import metadata
    assert set(migration.TABLES)=={t.name for t in metadata.tables.values()}
    assert migration.COLUMN_NAMES=={t.name:tuple(c.name for c in t.c) for t in metadata.tables.values()}
    for table in metadata.tables.values():
        assert [(r[2],r[4]) for r in structure['columns'] if r[0]==table.name]==[(c.name,not c.nullable) for c in table.c]
    # Full function bodies, trigger definitions and index attributes are in the artifact, not just counts.
    sql=Path('backend/migrations/sql/M007_guards.sql').read_text()
    bodies=dict(re.findall(r'CREATE FUNCTION public\.(\w+)\([^;]+? AS \$\$(.*?)\$\$;',sql,re.S))
    assert len(bodies)==19
    for row in structure['functions']:
        assert bodies[row[0]].strip() in row[9]
        assert row[4:9]==[False,'v','u',False,['search_path=pg_catalog, public']]


def test_offline_m008_only_applies_explicit_quoted_privileges():
    output=io.StringIO();config=config_for(output=output)
    assert ScriptDirectory.from_config(config).get_heads()==['M008']
    command.upgrade(config,'M007:M008',sql=True);ddl=output.getvalue()
    assert 'BEGIN;' in ddl and 'COMMIT;' in ddl and "version_num='M008'" in ddl
    assert ddl.count('GRANT EXECUTE ON FUNCTION')==14
    assert ddl.count('GRANT UPDATE (')==14
    assert 'GRANT SELECT ON TABLE public.schema_definitions' in ddl
    assert 'REVOKE ALL (version_num)' in ddl
    assert ddl.count('ALTER DEFAULT PRIVILEGES')==6
    assert not any(s in ddl for s in ('CREATE ROLE','CREATE TABLE','CREATE FUNCTION','INSERT INTO public.','DELETE FROM public.','WITH GRANT OPTION','GRANT ALL'))
    tree=ast.parse(Path('backend/migrations/versions/M008_grants.py').read_text())
    assert not any(isinstance(n,ast.ImportFrom) and (n.module or '').startswith('backend') for n in ast.walk(tree))


def test_m001_through_m007_and_packaged_m008_fingerprints():
    contract=json.loads(CONTRACT.read_text())
    for path,digest in contract['migration_sha256'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest,path
    prior=json.loads(Path('docs/DB_Settings/verification/2026-10-18-implementation-C07b.json').read_text())
    for item in prior['migration_sources']:
        assert contract['migration_sha256'][item['path']]==item['sha256']
    for item in prior['preserved_baseline_files']:
        if item['path'].startswith('backend/migrations/versions/'):
            assert contract['migration_sha256'][item['path']]==item['sha256']


def test_importing_readiness_performs_no_filesystem_network_or_engine_io():
    code='''
import builtins,socket
from pathlib import Path
import sqlalchemy
import backend.app.persistence.database

def denied(*a,**k): raise AssertionError('Unexpected I/O at import')
Path.read_text=denied
socket.create_connection=denied
sqlalchemy.create_engine=denied
import backend.app.persistence.schema_inventory
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('content',(None,'invalid','{}','{"revision":"M007","structure":{},"privileges":{}}'))
def test_missing_or_invalid_packaged_contract_is_safe(tmp_path,monkeypatch,content):
    from backend.app.persistence import schema_inventory as inventory
    from backend.app.persistence.database import PersistenceError
    path=tmp_path/'contract.json'
    if content is not None:path.write_text(content)
    monkeypatch.setattr(inventory,'CONTRACT_PATH',path)
    with pytest.raises(PersistenceError) as error:inventory._load_contract()
    assert error.value.code=='DB_SCHEMA_CONTRACT_INVALID'
    assert str(path) not in str(error.value)
