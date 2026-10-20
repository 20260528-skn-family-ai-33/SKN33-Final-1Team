"""Independent index contract, draft staging and copied metadata for C07a tests."""
from pathlib import Path
import re
import shutil

DRAFT = Path('backend/migrations/candidates/M007_indexes_draft.py')


def index_contract():
    source=Path('docs/DB_Settings/물리설계_06_제약목록.md').read_text().split('## 3. 추가 조회·부분 유일 인덱스')[1].split('## 4.')[0]
    result=[]
    for line in source.splitlines():
        m=re.search(r'^\| `(\w+)`( \*\*UNIQUE\*\*)? \| `(\w+)\(([^)]+)\)` \| ([^|]+) \|',line)
        if m:
            name,unique,table,columns,predicate=m.groups()
            keys=tuple((part.strip().split()[0],part.strip().endswith(' DESC')) for part in columns.split(','))
            predicate=predicate.strip().strip('`')
            result.append((name,table,keys,bool(unique),None if predicate=='전체 행' else predicate))
    return result


def preview_metadata():
    from sqlalchemy import MetaData
    from backend.app.persistence.schema import metadata
    from backend.app.persistence.schema.indexes import register_indexes
    result=MetaData(schema='public')
    for table in metadata.tables.values():
        table.to_metadata(result)
    for table in result.tables.values():
        table.indexes.clear()  # Rebuild independently even after M007 promotion.
    register_indexes(result)
    return result


def stage_index_draft(destination):
    directory=destination/'migrations'
    shutil.copytree('backend/migrations',directory,ignore=shutil.ignore_patterns('__pycache__'))
    for version in (directory/'versions').glob('M00[78]*.py'):
        version.unlink()  # Keep the C07a historical draft isolated from the promoted M007.
    shutil.copyfile(DRAFT,directory/'versions/M007_indexes_draft.py')
    return directory


def normalize_predicate(value):
    # Reviewed predicates are conjunctions of column equality/IS NOT NULL, no OR/functions.
    if value is None:
        return None
    value=re.sub(r'::(?:text|character varying)', '', str(value))
    return re.sub(r'[\s()]','',value).lower()
