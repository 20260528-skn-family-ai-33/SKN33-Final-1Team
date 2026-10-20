"""Pin pre-M006/M007 comparisons without mutating the final production metadata.

Only historical M001-M005 tests use this helper. Head inventory/FK tests inspect
all production relationships and the actual PostgreSQL constraints directly.
"""
from sqlalchemy import ForeignKeyConstraint, MetaData


def metadata_before_relations(names=None):
    from backend.app.persistence.schema import metadata as current
    selected = set(names) if names is not None else {t.name for t in current.tables.values()}
    result = MetaData(schema='public')
    for table in current.tables.values():
        if table.name in selected:
            table.to_metadata(result)
    assert {t.name for t in result.tables.values()} == selected
    for table in result.tables.values():
        table.indexes.clear()  # M007 additions do not belong to historical revisions.
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
                for element in constraint.elements:
                    element.parent.foreign_keys.discard(element)
                    table.foreign_keys.discard(element)
    return result


def foreign_key_contract():
    """Read the independent reviewed FK contract, never production definitions."""
    from pathlib import Path
    import re
    source = Path('docs/DB_Settings/물리설계_06_제약목록.md').read_text().split('## 3. 추가 조회')[0]
    result = []
    for section in re.split(r'^### ', source, flags=re.M)[1:]:
        table = section.splitlines()[0].strip()
        for line in section.splitlines():
            if line.startswith('| FK |'):
                match = re.search(r'\| FK \| `(\w+)` \| `\(([^)]+)\)` → `(\w+)\(([^)]+)\)`', line)
                assert match, line
                name, columns, target, references = match.groups()
                result.append((table, name, tuple(c.strip() for c in columns.split(',')), target,
                               tuple(c.strip() for c in references.split(','))))
    return result


def metadata_with_default_schema_alias(*, include_indexes=False):
    """Alembic reflects default public tables as schema=None; normalize only that alias.

    Keep every FK, column and policy. Explicit PostgreSQL catalog comparisons in
    the M006 tests still verify the real public schema and all FK attributes.
    """
    from sqlalchemy import BLANK_SCHEMA
    from backend.app.persistence.schema import metadata as current
    result = MetaData()
    for table in current.tables.values():
        table.to_metadata(result, schema=None,
                          referred_schema_fn=lambda table, to_schema, constraint, schema: BLANK_SCHEMA if schema == 'public' else schema)
    if not include_indexes:
        for table in result.tables.values():
            table.indexes.clear()
    return result
