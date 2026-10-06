"""Explicit, repeatable adoption of the original SQLite schema."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from django.core.management import call_command, CommandError
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder

LEGACY_COLUMNS = {
    'confirmations': {'version', 'confirmed_at'},
    'assets': {'id', 'document'},
    'runs': {'id', 'idem_key', 'fingerprint', 'created_at', 'document'},
}


def prepare_database(alias='default', verbosity=1):
    connection = connections[alias]
    backup = None
    try:
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
        applied = MigrationRecorder(connection).applied_migrations()
        legacy = set(LEGACY_COLUMNS) & tables
        if legacy and ('inspections', '0001_initial') not in applied:
            if legacy != set(LEGACY_COLUMNS):
                raise CommandError('기존 검수 테이블이 일부 누락되었습니다. 원본을 보존하고 이전을 중단합니다.')
            with connection.cursor() as cursor:
                for table, expected in LEGACY_COLUMNS.items():
                    actual = {column.name for column in connection.introspection.get_table_description(cursor, table)}
                    if actual != expected:
                        raise CommandError(f'{table} 스키마가 예상과 다릅니다. 원본을 보존하고 이전을 중단합니다.')
                    constraints = connection.introspection.get_constraints(cursor, table)
                    primary_key = 'version' if table == 'confirmations' else 'id'
                    if not any(item['primary_key'] and item['columns'] == [primary_key] for item in constraints.values()):
                        raise CommandError(f'{table} 기본 키를 확인할 수 없어 이전을 중단합니다.')
                    if table == 'runs' and not any(item['unique'] and item['columns'] == ['idem_key'] for item in constraints.values()):
                        raise CommandError('검수 요청 키의 중복 방지 제약이 없어 이전을 중단합니다.')
            source = Path(connection.settings_dict['NAME'])
            destination = source.parent / 'backups'
            destination.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            backup = destination / f'latte-before-django-{stamp}-{uuid4().hex[:8]}.sqlite3'
            connection.close()
            with sqlite3.connect(source) as original, sqlite3.connect(backup) as copy:
                original.backup(copy)
        call_command('migrate', database=alias, interactive=False, fake_initial=True, verbosity=verbosity)
        return backup
    finally:
        connection.close()
