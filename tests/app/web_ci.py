"""Explicit CI-only bootstrap for a new disposable PostgreSQL service, never an app DB."""
import os
import psycopg
from psycopg import sql


def main():
    assert os.environ.get('C12_CI_DISPOSABLE_POSTGRES')=='1','Disposable CI service flag required'
    with psycopg.connect(host=os.environ['TEST_DB_HOST'],port=os.environ['TEST_DB_PORT'],dbname='postgres',user=os.environ['TEST_DB_ADMIN_USER'],password=os.environ['TEST_DB_ADMIN_PASSWORD'],autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM pg_database WHERE datname NOT IN ('postgres','template0','template1')").fetchone()[0]==0
        for role,password in [('TEST_DB_OWNER_USER','TEST_DB_OWNER_PASSWORD'),('TEST_DB_APP_USER','TEST_DB_APP_PASSWORD')]:
            c.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(os.environ[role]),sql.Literal(os.environ[password])))


if __name__=='__main__':main()
