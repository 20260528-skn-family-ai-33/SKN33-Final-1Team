"""M007 DDL is frozen, explicit, privilege constrained and independently inventoried."""
import ast
import io
from pathlib import Path
import re

import pytest
pytest.importorskip('sqlalchemy')
pytest.importorskip('alembic')
from alembic import command
from alembic.script import ScriptDirectory
from backend.tests.test_index_schema import config_for

SQL_PATH=Path('backend/migrations/sql/M007_guards.sql')


def guard_contract():
    source=Path('docs/DB_Settings/contracts/schema/물리설계_06_제약목록.md').read_text().split('## 4. 전이·추가·커밋 검증 규칙')[1].split('## 5.')[0]
    result=[]
    for line in source.splitlines():
        match=re.match(r'\| `(\w+)` \| ([^|]+) \|',line)
        if match:result.extend((match[1],table) for table in re.findall(r'`(\w+)`',match[2]))
    return result


def test_all_30_rules_have_35_reviewed_trigger_instances():
    sql=SQL_PATH.read_text()
    actual=re.findall(r'CREATE (?:CONSTRAINT )?TRIGGER (\w+) (?:BEFORE|AFTER) [^;]+? ON public\.(\w+)\s',sql)
    assert sorted(actual)==sorted(guard_contract()) and len(actual)==35 and len({x[0] for x in actual})==30
    assert sql.count('DEFERRABLE INITIALLY DEFERRED')==18
    candidate=Path('backend/migrations/candidates/M007_indexes_draft.py').read_text()
    index_ddl=candidate[candidate.index('    op.create_index('):candidate.index('\n\ndef downgrade():')]
    assert index_ddl in Path('backend/migrations/versions/M007_guards.py').read_text()
    assert len(re.findall('CREATE FUNCTION public.',sql))==19


def test_offline_official_head_m007_includes_indexes_guards_and_no_app_grants():
    output=io.StringIO();config=config_for(output=output)
    assert ScriptDirectory.from_config(config).get_heads()==['M008']
    command.upgrade(config,'M006:M007',sql=True);ddl=output.getvalue()
    assert ddl.count('CREATE INDEX ')==25 and ddl.count('CREATE UNIQUE INDEX ')==2
    assert ddl.count('CREATE FUNCTION public.')==19 and ddl.count('CREATE CONSTRAINT TRIGGER ')==18
    assert 'BEGIN;' in ddl and 'COMMIT;' in ddl and "version_num='M007'" in ddl
    assert not any(word in ddl for word in ('CREATE ROLE','GRANT ','CREATE OR REPLACE','DISABLE TRIGGER','SECURITY DEFINER'))
    tree=ast.parse(Path('backend/migrations/versions/M007_guards.py').read_text())
    assert not any(isinstance(n,ast.ImportFrom) and (n.module or '').startswith('backend') for n in ast.walk(tree))


def test_functions_use_fixed_search_path_and_no_public_execution_or_mutable_bypass():
    sql=SQL_PATH.read_text()
    functions=re.findall(r'CREATE FUNCTION public\.(\w+)\(([^)]*)\)',sql)
    revoked=re.findall(r'REVOKE ALL ON FUNCTION public\.(\w+)\(',sql)
    assert {x[0] for x in functions}==set(revoked) and len(revoked)==19
    assert sql.count('LANGUAGE plpgsql SET search_path = pg_catalog, public')==19
    assert not any(word in sql.lower() for word in ('security definer','set_config(', 'session_replication_role','pg_trigger_depth', 'disable trigger'))
