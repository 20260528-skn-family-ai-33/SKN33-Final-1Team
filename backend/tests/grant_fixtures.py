"""C07c expectations read the reviewed policy, independently of M008/runtime JSON."""
from pathlib import Path
import re


def update_policy():
    source=Path('docs/DB_Settings/contracts/schema/물리설계_06_전체정합성.md').read_text()
    section=source.split('| 테이블 | UPDATE 허용 컬럼 |')[1].split('관리자의 DDL')[0]
    result={}
    for line in section.splitlines():
        match=re.match(r'^\| (\w+) \| ([^|]+) \|',line)
        if match:result[match[1]]=tuple(c.strip() for c in match[2].split('.')[0].split(','))
    assert len(result)==14 and sum(map(len,result.values()))==82
    return result


def helper_functions():
    # Four trigger entry points and owner-only catalog validation stay ungranted.
    return {'cc_fail','cc_lock_project','cc_check_owner','cc_check_bounds','cc_check_frame',
        'cc_lock_live_run','cc_gate_owner','cc_check_evidence','cc_check_ocr','cc_check_extraction',
        'cc_check_criteria','cc_check_review','cc_check_import','cc_check_lineage'}


def expected_privileges(structure):
    updates=update_policy();tables=[];columns=[]
    for row in structure['relations']:
        table=row[0]
        for privilege in sorted(('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER','MAINTAIN')):
            allowed=privilege=='SELECT' or privilege=='INSERT' and table not in ('schema_definitions','alembic_version')
            tables.append([table,privilege,allowed,False])
    for table,_,column,*_ in sorted(structure['columns'],key=lambda row:(row[0],row[2])):
        for privilege in sorted(('SELECT','INSERT','UPDATE','REFERENCES')):
            allowed=(privilege=='SELECT' or privilege=='INSERT' and table not in ('schema_definitions','alembic_version')
                or privilege=='UPDATE' and column in updates.get(table,()))
            columns.append([table,column,privilege,allowed,False])
    functions=[[r[0],r[0] in helper_functions(),False] for r in structure['functions']]
    return {'tables':tables,'columns':columns,'functions':functions,
        'boundary':[True,False,True,False,False,False,False,False,False],
        'public_grants':[],'default_grants':[]}
