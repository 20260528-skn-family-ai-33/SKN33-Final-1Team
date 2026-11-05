"""Initialize only the fresh disposable servers, then run Linux/POSIX tests."""
import hashlib
import os
import subprocess
import sys

PURPOSE='agent-framework-disposable-tests'


def initialize(env):
    import psycopg
    from psycopg import sql
    from neo4j import GraphDatabase
    with psycopg.connect(host=env['TEST_DB_HOST'],port=env['TEST_DB_PORT'],dbname='postgres',
            user=env['TEST_DB_ADMIN_USER'],password=env['TEST_DB_ADMIN_PASSWORD'],
            connect_timeout=5,autocommit=True) as c:
        for name,password in [('TEST_DB_OWNER_USER','TEST_DB_OWNER_PASSWORD'),('TEST_DB_APP_USER','TEST_DB_APP_PASSWORD')]:
            c.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(env[name]),sql.Literal(env[password])))
    with GraphDatabase.driver(env['AF_TEST_NEO4J_URI'],auth=('neo4j',env['AF_TEST_NEO4J_PASSWORD']),
            connection_timeout=5,max_transaction_retry_time=5) as driver:
        with driver.session(database='neo4j') as session:
            if session.run('MATCH(n) RETURN count(n) AS count').single()['count']:
                raise RuntimeError('REFUSED_NONEMPTY_TEST_GRAPH')
            session.run('CREATE(:AFTestEnvironment {purpose:$purpose,run_id:$run,token_sha256:$token})',
                purpose=PURPOSE,run=env['AF_TEST_NEO4J_RUN_ID'],
                token=hashlib.sha256(env['AF_TEST_NEO4J_TOKEN'].encode()).hexdigest()).consume()


def main():
    try:initialize(os.environ)
    except Exception:
        print('ISOLATED_DATABASE_INITIALIZATION_FAILED',flush=True);return 2
    return subprocess.run([sys.executable,'-m','pytest',*sys.argv[1:]],shell=False).returncode


if __name__=='__main__':raise SystemExit(main())
