"""Explicit, read-only M008 catalog/privilege readiness. Import performs no I/O."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .database import PersistenceError

CONTRACT_PATH = Path(__file__).with_name('schema_contract.json')
EXPECTED_REVISION = 'M008'

# Catalog descriptions deliberately exclude OIDs, current data and role names.
_STRUCTURE_QUERIES = {
    'relations': """SELECT c.relname,c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,
        c.relowner=d.datdba FROM pg_class c CROSS JOIN pg_database d
        WHERE d.datname=current_database() AND c.relnamespace='public'::regnamespace
        AND c.relkind IN ('r','p','v','m','f','S') ORDER BY c.relname""",
    'columns': """SELECT c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
        pg_get_expr(ad.adbin,ad.adrelid),a.attidentity,a.attgenerated,a.attcollation::regcollation::text
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
        WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p','v','m','f')
        AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum""",
    'constraints': """SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid,true),
        k.convalidated,k.condeferrable,k.condeferred,k.connoinherit
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        WHERE k.connamespace='public'::regnamespace ORDER BY c.relname,k.conname""",
    'indexes': """SELECT t.relname,c.relname,pg_get_indexdef(i.indexrelid),
        i.indisvalid,i.indisready,i.indislive,i.indisunique,i.indnullsnotdistinct
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_class t ON t.oid=i.indrelid
        WHERE c.relnamespace='public'::regnamespace ORDER BY t.relname,c.relname""",
    'triggers': """SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid,true),t.tgenabled,
        t.tgdeferrable,t.tginitdeferred,t.tgtype,encode(t.tgargs,'hex')
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        WHERE c.relnamespace='public'::regnamespace AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""",
    'functions': """SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_function_result(p.oid),
        l.lanname,p.prosecdef,p.provolatile,p.proparallel,p.proleakproof,p.proconfig,
        pg_get_functiondef(p.oid),p.proowner=d.datdba
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang CROSS JOIN pg_database d
        WHERE d.datname=current_database() AND p.pronamespace='public'::regnamespace
        ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)""",
    'types': """SELECT t.typname,t.typtype,t.typcategory,t.typnotnull,
        t.typelem::regtype::text,t.typbasetype::regtype::text,t.typowner=d.datdba
        FROM pg_type t CROSS JOIN pg_database d WHERE d.datname=current_database()
        AND t.typnamespace='public'::regnamespace ORDER BY t.typname""",
    'policies': """SELECT tablename,policyname,permissive,roles,cmd,qual,with_check
        FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname""",
}


def capture_structure(connection):
    """Caller owns the transaction. Used only by explicit checks and test evidence."""
    connection.exec_driver_sql('SET LOCAL search_path = pg_catalog, public')
    return {name:[list(row) for row in connection.exec_driver_sql(sql).all()]
            for name,sql in _STRUCTURE_QUERIES.items()}


def capture_privileges(connection, app_user):
    from sqlalchemy import text
    tables=connection.execute(text("""SELECT c.relname,p.privilege,
        has_table_privilege(:app,c.oid,p.privilege),has_table_privilege(:app,c.oid,p.privilege||' WITH GRANT OPTION')
        FROM pg_class c CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('REFERENCES'),('TRIGGER'),('MAINTAIN')) p(privilege)
        WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p') ORDER BY c.relname,p.privilege"""),{'app':app_user}).all()
    columns=connection.execute(text("""SELECT c.relname,a.attname,p.privilege,
        has_column_privilege(:app,c.oid,a.attnum,p.privilege),has_column_privilege(:app,c.oid,a.attnum,p.privilege||' WITH GRANT OPTION')
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('REFERENCES')) p(privilege)
        WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped
        ORDER BY c.relname,a.attname,p.privilege"""),{'app':app_user}).all()
    functions=connection.execute(text("""SELECT p.proname,has_function_privilege(:app,p.oid,'EXECUTE'),
        has_function_privilege(:app,p.oid,'EXECUTE WITH GRANT OPTION')
        FROM pg_proc p WHERE p.pronamespace='public'::regnamespace ORDER BY p.proname"""),{'app':app_user}).all()
    boundary=connection.execute(text("""SELECT has_schema_privilege(:app,'public','USAGE'),has_schema_privilege(:app,'public','CREATE'),
        has_database_privilege(:app,current_database(),'CONNECT'),has_database_privilege(:app,current_database(),'CREATE'),
        has_database_privilege(:app,current_database(),'TEMPORARY'),
        EXISTS(SELECT 1 FROM pg_roles r JOIN pg_roles a ON a.rolname=:app
          WHERE r.oid<>a.oid AND pg_has_role(a.oid,r.oid,'MEMBER')),
        EXISTS(SELECT 1 FROM pg_roles a WHERE a.rolname=:app AND
          (a.rolsuper OR a.rolcreatedb OR a.rolcreaterole OR a.rolreplication OR a.rolbypassrls)),
        has_schema_privilege(:app,'public','USAGE WITH GRANT OPTION'),
        has_database_privilege(:app,current_database(),'CONNECT WITH GRANT OPTION')"""),{'app':app_user}).one()
    public=connection.exec_driver_sql("""SELECT 'table',c.relname,x.privilege_type FROM pg_class c
        CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x
        WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p') AND x.grantee=0
        UNION ALL SELECT 'column',c.relname||'.'||a.attname,x.privilege_type FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        CROSS JOIN LATERAL aclexplode(a.attacl) x
        WHERE c.relnamespace='public'::regnamespace AND a.attnum>0 AND NOT a.attisdropped AND x.grantee=0
        UNION ALL SELECT 'function',p.proname,x.privilege_type FROM pg_proc p
        CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
        WHERE p.pronamespace='public'::regnamespace AND x.grantee=0 ORDER BY 1,2,3""").all()
    defaults=connection.execute(text("""SELECT scope.label,k.kind,x.privilege_type
        FROM pg_database d CROSS JOIN (VALUES (0::oid,'global'),('public'::regnamespace::oid,'public')) scope(ns,label)
        CROSS JOIN (VALUES ('r'::"char"),('S'::"char"),('f'::"char")) k(kind)
        LEFT JOIN pg_default_acl a ON a.defaclrole=d.datdba AND a.defaclnamespace=scope.ns AND a.defaclobjtype=k.kind
        CROSS JOIN LATERAL aclexplode(coalesce(a.defaclacl,CASE WHEN scope.ns=0 THEN acldefault(k.kind,d.datdba) ELSE NULL::aclitem[] END)) x
        WHERE d.datname=current_database() AND x.grantee IN (0,(SELECT oid FROM pg_roles WHERE rolname=:app))
        ORDER BY 1,2,3"""),{'app':app_user}).all()
    return {'tables':[list(r) for r in tables],'columns':[list(r) for r in columns],
            'functions':[list(r) for r in functions],'boundary':list(boundary),
            'public_grants':[list(r) for r in public],'default_grants':[list(r) for r in defaults]}


def _load_contract():
    try:
        result=json.loads(CONTRACT_PATH.read_text(encoding='utf-8'))
        if result['revision']!=EXPECTED_REVISION or not isinstance(result['structure'],dict) or not isinstance(result['privileges'],dict):
            raise ValueError()
        return result
    except (OSError,ValueError,KeyError,TypeError):
        raise PersistenceError('DB_SCHEMA_CONTRACT_INVALID','The packaged schema contract is unavailable or invalid.') from None


def validate_schema(connection, *, app_user):
    """Read-only; never repairs drift or trusts the revision alone. Caller owns transaction."""
    from sqlalchemy.exc import SQLAlchemyError
    expected=_load_contract()
    try:
        rows=connection.exec_driver_sql('SELECT version_num FROM public.alembic_version ORDER BY version_num').scalars().all()
        if rows!=[EXPECTED_REVISION]:
            raise PersistenceError('DB_SCHEMA_INCOMPLETE','The required M008 schema has not been applied.')
        actual=capture_structure(connection)
        if actual!=expected['structure']:
            raise PersistenceError('DB_SCHEMA_DRIFT','The database structure differs from the reviewed M008 contract.')
        privileges=capture_privileges(connection,app_user)
        if privileges!=expected['privileges']:
            raise PersistenceError('DB_PRIVILEGES_INVALID','The database privileges differ from the reviewed M008 contract.')
    except SQLAlchemyError as error:
        state=getattr(getattr(error,'orig',None),'sqlstate',None)
        raise PersistenceError('DB_SCHEMA_UNREADABLE','The required schema cannot be inspected with this connection.',sqlstate=state) from None
    encoded=json.dumps(actual,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    return {'status':'schema_ready','revision':EXPECTED_REVISION,'business_tables':26,'columns':395,
            'foreign_keys':81,'additional_indexes':27,'trigger_instances':35,'functions':19,
            'structure_sha256':hashlib.sha256(encoded).hexdigest(),'scope':'database_schema_and_privileges'}


def check_schema(engine,settings):
    """Standalone schema preflight; API/profile startup integration is C08."""
    from .transaction import unit_of_work
    from .database import _IDENTITY_SQL, _validate_identity
    with unit_of_work(engine) as connection:
        connection.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        _validate_identity(connection.exec_driver_sql(_IDENTITY_SQL).one(), settings.database)
        return validate_schema(connection,app_user=settings.database.username)
