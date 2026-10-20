"""Explicit isolated PostgreSQL/E2E runner. Never falls back to the application's .env."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from backend.app.config import read_environment_file

ROOT=Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',choices=('core','operations','release','deployment','worker','browser','https','ocr','worker-ocr'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--guard-env',type=Path,help='Optional explicit daily DB environment: read-only before/after comparison only.')
    args=parser.parse_args(argv)
    values=read_environment_file(Path(os.environ['TEST_DB_ENV_FILE'])) if os.environ.get('TEST_DB_ENV_FILE') else os.environ
    needed=('TEST_DB_ALLOW_CREATE','TEST_DB_HOST','TEST_DB_PORT','TEST_DB_ADMIN_USER','TEST_DB_ADMIN_PASSWORD','TEST_DB_OWNER_USER','TEST_DB_OWNER_PASSWORD','TEST_DB_APP_USER','TEST_DB_APP_PASSWORD')
    if values.get('TEST_DB_ALLOW_CREATE')!='1' or any(not values.get(key) for key in needed):
        parser.error('Explicit TEST_DB_* settings are required; app .env fallback is disabled.')
    if args.suite=='https' and (values.get('C12_RUN_HTTPS')!='1' or not values.get('C12_API_IMAGE')):parser.error('HTTPS requires C12_RUN_HTTPS=1 and C12_API_IMAGE.')
    if args.suite=='ocr' and values.get('C12_RUN_REAL_OCR')!='1':parser.error('Actual OCR requires C12_RUN_REAL_OCR=1.')
    if args.suite=='worker-ocr' and values.get('C13_RUN_REAL_OCR')!='1':parser.error('Actual worker OCR requires C13_RUN_REAL_OCR=1.')
    if args.suite=='deployment' and (values.get('C15_RUN_DEPLOYMENT')!='1' or not values.get('C15_API_IMAGE')):parser.error('Deployment rehearsal requires C15_RUN_DEPLOYMENT=1 and C15_API_IMAGE.')
    import psycopg
    def snapshot():
        with psycopg.connect(host=values['TEST_DB_HOST'],port=values['TEST_DB_PORT'],dbname='postgres',user=values['TEST_DB_ADMIN_USER'],password=values['TEST_DB_ADMIN_PASSWORD'],connect_timeout=5) as c:
            state={'temporary_databases':[row[0] for row in c.execute("SELECT datname FROM pg_database WHERE starts_with(datname,'creative_checker_test_') ORDER BY datname")],'postgres_version_num':c.info.server_version}
        if args.guard_env:
            guard=read_environment_file(args.guard_env,{})
            with psycopg.connect(host=guard['DB_HOST'],port=guard['DB_PORT'],dbname=guard['POSTGRES_DB'],user='postgres',password=guard['POSTGRES_PASSWORD'],connect_timeout=5) as c:
                state['daily_public_relations']=c.execute("SELECT n.nspname,c.relname,c.relkind,c.oid::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname").fetchall()
                state['default_privileges']=c.execute('SELECT defaclrole::regrole::text,defaclnamespace,defaclobjtype,defaclacl::text FROM pg_default_acl ORDER BY 1,2,3').fetchall()
        return state
    before=snapshot();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True);env=os.environ.copy();env.update(values)
    selection={'core':['backend/tests','--ignore=backend/tests/test_c12_live_integration.py','--ignore=backend/tests/test_c12_https_integration.py','--ignore=backend/tests/test_c15_deployment_integration.py','-m','db_integration and not ocr_external'],
        'release':['backend/tests/test_c15_release_integration.py','-m','db_integration'],
        'deployment':['backend/tests/test_c15_deployment_integration.py','-m','db_integration'],
        'operations':['backend/tests/test_c14_import_gc_integration.py','backend/tests/test_c14_backup_integration.py','-m','db_integration and not ocr_external'],
        'worker':['backend/tests/test_c13_worker_integration.py','backend/tests/test_c13_process_integration.py','-m','db_integration and not ocr_external'],
        'browser':['backend/tests/test_c12_live_integration.py','-m','db_integration and not ocr_external'],
        'https':['backend/tests/test_c12_https_integration.py','-m','db_integration'],
        'ocr':['backend/tests/test_c12_live_integration.py','-m','db_integration and ocr_external'],
        'worker-ocr':['backend/tests/test_c13_real_ocr_integration.py','-m','db_integration and ocr_external']}[args.suite]
    result=subprocess.run([sys.executable,'-m','pytest',*selection,'-q','--tb=short','-x','--basetemp='+str(out/'fixtures'),'--junitxml='+str(out/'pytest.xml')],cwd=ROOT,env=env)
    after=snapshot();report={'suite':args.suite,'pytest_exit_code':result.returncode,'before':before,'after':after,'guard_unchanged':before==after,'daily_guard_executed':args.guard_env is not None}
    if (out/'pytest.xml').exists():
        document=ET.parse(out/'pytest.xml').getroot();cases=list(document.iter('testcase'));report['unique_cases']=len(cases);report['failed_cases']=sum(row.find('failure') is not None or row.find('error') is not None for row in cases)
    (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    assert before==after,'Database guard changed; investigate owned test resources without FORCE or unrelated session termination.'
    return result.returncode


if __name__=='__main__':raise SystemExit(main())
