"""Natural PostgreSQL plans on bounded synthetic data; no production speed claims."""
from datetime import datetime,timedelta,timezone
import json
from uuid import uuid4

import pytest

from backend.tests.test_db_integration import database  # noqa: F401
from backend.tests.test_relations_migration_integration import relations_database  # noqa: F401
from backend.tests.test_index_migration_integration import indexes_database  # noqa: F401

pytestmark=pytest.mark.db_integration


def plan_nodes(node):
    yield node
    for child in node.get('Plans',[]):yield from plan_nodes(child)


@pytest.fixture(scope='module')
def query_workload(indexes_database):
    from sqlalchemy import bindparam
    from backend.app.persistence.schema import metadata
    from backend.tests.test_review_migration_integration import service_run,service_attempt
    _,owner,_,main,other_image,other_project=indexes_database
    graphs=[main,other_image,other_project];base=datetime.now(timezone.utc)
    runs=metadata.tables['public.review_runs'];attempts=metadata.tables['public.review_attempts']
    run_rows=[];attempt_rows=[];running_updates=[];asset_rows=[];audit_rows=[]
    nullable_runs={c.name:None for c in runs.c if c.nullable}
    nullable_attempts={c.name:None for c in attempts.c if c.nullable}
    for i in range(6000):
        graph=graphs[i%3];project=graph['projects']['project_id'];asset=graph['asset_versions']['asset_version_id']
        created=base-timedelta(seconds=i//9)
        run={**nullable_runs,'run_id':uuid4(),'project_id':project,'asset_version_id':asset,
             'criteria_version_id':graph['criteria_versions']['criteria_version_id'],'origin_kind':'legacy_result',
             'execution_status':'imported','executor_kind':'imported','orchestration_mode':'imported',
             'source_snapshot_jsonb':{'fixture':i},'created_at':created,'updated_at':created}
        slot=i%8
        if slot<4:
            run.update(service_run('failed' if slot==3 else 'queued'),requested_by=main['users']['user_id'])
            run['executor_kind']='postgres_worker' if slot==0 or i%16==2 else 'background_tasks'
            if slot!=3:run['available_at']=base+timedelta(seconds=i%200-100)
        if slot==2:
            attempt={**nullable_attempts,**service_attempt(),'attempt_id':uuid4(),'project_id':project,'asset_version_id':asset,
                     'run_id':run['run_id'],'attempt_no':1,'created_at':created}
            if run['executor_kind']=='postgres_worker':
                attempt.update(lease_expires_at=base+timedelta(seconds=i%200-100),heartbeat_at=base+timedelta(seconds=i%200-190))
            attempt_rows.append(attempt)
            running_updates.append({'target_run':run['run_id'],'new_attempt':attempt['attempt_id'],'new_start':attempt['started_at']})
        elif slot>=4:
            attempt_rows.append({**nullable_attempts,'attempt_id':uuid4(),'project_id':project,'asset_version_id':asset,
                'run_id':run['run_id'],'attempt_no':1,'execution_status':'imported','source_attempt_jsonb':{'fixture':i},'created_at':created})
        run_rows.append(run)
        new_asset=uuid4()
        asset_rows.append({'asset_id':new_asset,'project_id':project,'parent_asset_id':graph['assets']['asset_id'] if i%100==0 else None,
            'asset_kind':'poster_image','name':'C07a synthetic asset','created_by':main['users']['user_id'],'created_at':created,'updated_at':created})
        audit_rows.append({'event_id':uuid4(),'project_id':project,'actor_kind':'developer','actor_user_id':main['users']['user_id'],
            'action':'fixture.query','target_type':'asset','target_id':new_asset,'occurred_at':created})
    users=[];members=[]
    for i in range(2000):
        user=uuid4();graph=graphs[i%3]
        users.append({'user_id':user,'account_kind':'email','email':f'c07a-plan-{i}@example.invalid',
                      'password_hash':'fixture-only-not-a-real-password','display_name':'synthetic plan subject'})
        members.append({'project_id':graph['projects']['project_id'],'user_id':user,'role':'viewer','is_active':i%4!=0})
    with owner.begin() as connection:
        connection.execute(runs.insert(),run_rows)
        connection.execute(attempts.insert(),attempt_rows)
        connection.execute(runs.update().where(runs.c.run_id==bindparam('target_run')).values(execution_status='running',
            current_stage='ocr',active_attempt_id=bindparam('new_attempt'),started_at=bindparam('new_start'),available_at=None,progress=50),running_updates)
        connection.execute(metadata.tables['public.assets'].insert(),asset_rows)
        connection.execute(metadata.tables['public.audit_events'].insert(),audit_rows)
        connection.execute(metadata.tables['public.users'].insert(),users)
        connection.execute(metadata.tables['public.project_members'].insert(),members)
        for name in ('review_runs','review_attempts','assets','audit_events','users','project_members'):
            connection.exec_driver_sql('ANALYZE public.'+name)
    return {'owner':owner,'project':main['projects']['project_id'],'member':users[1001]['user_id'],
            'audit_project':audit_rows[1001]['project_id'],'audit_target':audit_rows[1001]['target_id'],
            'seed_counts':{'review_runs':6000,'review_attempts':len(attempt_rows),'assets':6000,'audit_events':6000,'users':2000,'project_members':2000}}


PLAN_QUERIES=[
 ('history','ix_review_runs_project_recent','SELECT run_id,created_at FROM public.review_runs WHERE project_id=:project ORDER BY created_at DESC,run_id DESC LIMIT 20'),
 ('status_history','ix_review_runs_project_status',"SELECT run_id,created_at FROM public.review_runs WHERE project_id=:project AND execution_status='queued' ORDER BY created_at DESC,run_id DESC LIMIT 20"),
 ('worker_candidates','ix_review_runs_queue',"SELECT run_id,available_at FROM public.review_runs WHERE origin_kind='service_execution' AND executor_kind='postgres_worker' AND execution_status='queued' AND available_at<=:cutoff ORDER BY available_at,created_at,run_id LIMIT 20"),
 ('lease_candidates','ix_review_attempts_lease',"SELECT attempt_id,lease_expires_at FROM public.review_attempts WHERE execution_status='active' AND lease_expires_at IS NOT NULL AND lease_expires_at<=:cutoff ORDER BY lease_expires_at,attempt_id LIMIT 20"),
 ('membership','ix_project_members_user_projects','SELECT project_id FROM public.project_members WHERE user_id=:member AND is_active=true ORDER BY project_id LIMIT 20'),
 ('asset_history','ix_assets_project_recent','SELECT asset_id,created_at FROM public.assets WHERE project_id=:project ORDER BY created_at DESC,asset_id DESC LIMIT 20'),
 ('audit_history','ix_audit_events_project_recent','SELECT event_id,occurred_at FROM public.audit_events WHERE project_id=:project ORDER BY occurred_at DESC,event_id DESC LIMIT 20'),
 ('audit_target','ix_audit_events_target',"SELECT event_id,occurred_at FROM public.audit_events WHERE project_id=:audit_project AND target_type='asset' AND target_id=:audit_target ORDER BY occurred_at DESC LIMIT 20"),
]


@pytest.mark.parametrize('name,expected_index,sql',PLAN_QUERIES,ids=[q[0] for q in PLAN_QUERIES])
def test_natural_planner_uses_representative_access_path(query_workload,request,name,expected_index,sql):
    from sqlalchemy import text
    owner=query_workload['owner']
    with owner.connect() as connection:
        assert connection.exec_driver_sql('SHOW enable_seqscan').scalar_one()=='on'
        params={k:v for k,v in query_workload.items() if k not in ('owner','seed_counts')}
        params['cutoff']=connection.exec_driver_sql('SELECT clock_timestamp()').scalar_one()
        plan=connection.execute(text('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+sql),params).scalar_one()[0]
        indexes={n['Index Name'] for n in plan_nodes(plan['Plan']) if 'Index Name' in n}
        assert expected_index in indexes,plan
        assert plan['Plan']['Actual Rows']>0
        request.node.user_properties.append(('query_plan',json.dumps({'case':name,'expected_index':expected_index,
            'seed_counts':query_workload['seed_counts'],'enable_seqscan':'on','plan':plan},default=str)))


def test_cursor_pagination_handles_equal_timestamps_without_duplicates(query_workload,request):
    from sqlalchemy import text
    with query_workload['owner'].connect() as connection:
        sql='SELECT run_id,created_at FROM public.review_runs WHERE project_id=:project ORDER BY created_at DESC,run_id DESC LIMIT '
        first=connection.execute(text(sql+'20'),{'project':query_workload['project']}).all()
        cursor_sql='SELECT run_id,created_at FROM public.review_runs WHERE project_id=:project AND (created_at,run_id)<(:time,:run) ORDER BY created_at DESC,run_id DESC LIMIT 20'
        params={'project':query_workload['project'],'time':first[-1][1],'run':first[-1][0]}
        second=connection.execute(text(cursor_sql),params).all()
        whole=connection.execute(text(sql+'40'),{'project':query_workload['project']}).all()
        assert first+second==whole and len({r[0] for r in first+second})==40
        assert first[-1][1]==second[0][1]  # The page boundary actually splits a timestamp tie.
        plan=connection.execute(text('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+cursor_sql),params).scalar_one()[0]
        assert 'ix_review_runs_project_recent' in {n.get('Index Name') for n in plan_nodes(plan['Plan'])}
        request.node.user_properties.append(('query_plan',json.dumps({'case':'cursor','seed_counts':query_workload['seed_counts'],'plan':plan},default=str)))


def test_generic_queue_plan_requires_fixed_predicates_in_query(query_workload,request):
    with query_workload['owner'].connect() as connection:
        connection.exec_driver_sql("SET LOCAL plan_cache_mode='force_generic_plan'")
        connection.exec_driver_sql("PREPARE c07a_fixed(timestamptz) AS SELECT run_id FROM public.review_runs WHERE origin_kind='service_execution' AND executor_kind='postgres_worker' AND execution_status='queued' AND available_at<=$1 ORDER BY available_at,created_at,run_id LIMIT 20")
        connection.exec_driver_sql('PREPARE c07a_bound(text,text,text,timestamptz) AS SELECT run_id FROM public.review_runs WHERE origin_kind=$1 AND executor_kind=$2 AND execution_status=$3 AND available_at<=$4 ORDER BY available_at,created_at,run_id LIMIT 20')
        fixed=connection.exec_driver_sql('EXPLAIN (FORMAT JSON) EXECUTE c07a_fixed(CURRENT_TIMESTAMP)').scalar_one()[0]
        bound=connection.exec_driver_sql("EXPLAIN (FORMAT JSON) EXECUTE c07a_bound('service_execution','postgres_worker','queued',CURRENT_TIMESTAMP)").scalar_one()[0]
        assert 'ix_review_runs_queue' in {n.get('Index Name') for n in plan_nodes(fixed['Plan'])}
        assert 'ix_review_runs_queue' not in {n.get('Index Name') for n in plan_nodes(bound['Plan'])}
        request.node.user_properties.append(('generic_plan_comparison',json.dumps({'fixed_predicates':fixed,'bound_predicates':bound},default=str)))
        connection.exec_driver_sql('DEALLOCATE c07a_fixed');connection.exec_driver_sql('DEALLOCATE c07a_bound')
