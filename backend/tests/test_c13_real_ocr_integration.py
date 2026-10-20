"""Explicit one-poster real local Paddle through production CLI worker and HTTP API."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time
from uuid import UUID,uuid4
import httpx,pytest
from sqlalchemy import select
from backend.tests.test_db_integration import database
from backend.tests.test_v1_auth_integration import auth_database,email_user,login,csrf
from backend.tests.auth_fixtures import auth_environment
from backend.tests.test_c09_business_integration import project,upload,post,confirm
from backend.tests.c12_helpers import server,clean_environment,process
from backend.app.storage.local import LocalStorage
from backend.app.services.business import table,one
from backend.app.persistence.transaction import unit_of_work

pytestmark=[pytest.mark.db_integration,pytest.mark.ocr_external]


def test_production_worker_cli_frozen_description_and_one_real_paddle_poster(auth_database,tmp_path,request):
    if os.environ.get('C13_RUN_REAL_OCR')!='1':pytest.fail('Explicit C13_RUN_REAL_OCR=1 required; actual OCR is never silently skipped.')
    db=auth_database[0];root=tmp_path.resolve()/'files';LocalStorage.initialize(root)
    values=auth_environment(db,STORAGE_ROOT=str(root),REVIEW_EXECUTOR='postgres_worker',REVIEW_PIPELINE='paddle-only',LOCAL_ADMIN_LOGIN='0')
    values={k:v for k,v in values.items() if not k.startswith('AUTH_') and k not in ('CSRF_SECRET','CURSOR_SECRET','DEV_PROJECT_ID')}
    envfile=tmp_path/'worker.env';fd=os.open(envfile,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write('\n'.join(k+'='+v for k,v in values.items())+'\n')
    config=tmp_path/'worker-config.json';user=email_user(auth_database);image_path=Path('data/img/2026_김천_김밥축제.jpg')
    command=[sys.executable,'-m','backend.app.worker','--env-file',str(envfile)]
    try:
        with (tmp_path/'worker.log').open('w+') as log:
            created=subprocess.run([*command,'--write-config',str(config)],env=clean_environment(),stdout=log,stderr=log,timeout=35)
            assert created.returncode==0,'Verified local worker description could not be produced.'
            with server(db,root,log,pipeline='disabled',executor='postgres_worker',worker_config=config) as (url,_):
                with httpx.Client(base_url=url,headers={'Origin':url},trust_env=False,timeout=15) as client:
                    assert login(client,email=user['email']).status_code==200
                    pid=project(client);image=upload(client,pid,image_path.read_bytes()).json()
                    sid=post(client,'/projects/'+pid+'/criteria-sets',{'name':'실제 worker OCR 기준'}).json()['id']
                    draft=post(client,'/projects/'+pid+'/criteria-drafts',dict(criteria_set_id=sid,image_id=image['id'],raw_requirements='축제명 표기',
                        items=[dict(id='name',text='행사명 표기',type='information',source='원문',selected=True)])).json()
                    version=confirm(client,pid,draft).json()
                    response=post(client,'/reviews',dict(project_id=pid,image_id=image['id'],criteria_version_id=version['id']),**{'Idempotency-Key':str(uuid4())})
                    assert response.status_code==202,response.text
                    run_id=UUID(response.json()['id']);assert response.json()['status']=='queued'
                    started=time.perf_counter()
                    with process([*command,'--once'],log,env=clean_environment()) as child:
                        assert child.wait(timeout=240)==0,'Owned real Paddle worker did not finish normally.'
                    elapsed=time.perf_counter()-started
                    data=client.get('/api/v1/reviews/'+str(run_id)).json()
                    assert data['status']=='completed' and data['results_complete'],data
                    ocr=client.get(data['ocr']['records_url']).json();assert ocr['items']
                    with unit_of_work(db.engine) as c:
                        run=one(c,'review_runs','run_id',run_id)
                        attempts=c.execute(select(table('review_attempts')).where(table('review_attempts').c.run_id==run_id)).mappings().all()
                        assert len(attempts)==1 and attempts[0]['execution_status']=='succeeded' and attempts[0]['remote_job_id'] is None
                        source=c.execute(select(table('stored_files')).join(table('ocr_runs'),table('stored_files').c.file_id==table('ocr_runs').c.source_file_id)
                            .where(table('ocr_runs').c.attempt_id==attempts[0]['attempt_id'])).mappings().one()
                    storage=LocalStorage(root)
                    try:
                        with storage.verified(source['storage_key'],source['byte_size'],source['sha256']) as stream:payload=json.load(stream)
                    finally:storage.close()
                    summary=dict(scope='one_real_poster_local_cpu_separate_production_cli_worker',poster_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest(),
                        display_dimensions=[image['width'],image['height']],
                        input_dimensions=payload['input_dimensions'],ocr_records=len(ocr['items']),criteria_items=len(data['results']),
                        worker_attempts=len(attempts),run_status=data['status'],profile=payload.get('profile'),worker_process_seconds=round(elapsed,4),
                        automatic_remote_submissions=0,accuracy_evaluation=False)
                    (tmp_path/'actual-worker-paddle.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
                    request.node.user_properties.append(('c13_actual_worker_ocr',json.dumps(summary,ensure_ascii=False)))
    finally:envfile.unlink()
