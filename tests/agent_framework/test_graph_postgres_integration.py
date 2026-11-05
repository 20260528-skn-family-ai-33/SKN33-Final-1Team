"""Actual isolated PG and AF-only Neo4j; all model and approval inputs are scripted."""
from uuid import uuid4
import pytest
from sqlalchemy import text
from backend.app.persistence.transaction import unit_of_work
from backend.agent_framework.postgres_schema import migrate,check_extension,check_app_permissions
from backend.agent_framework.postgres import PostgresStore,digest
from backend.agent_framework.composition import TeamConfiguration
from backend.agent_framework.criteria_review import PostgresCriteriaReview
from backend.agent_framework.poster_review import PostgresPosterReviews
from backend.agent_framework.durable_worker import PostgresQueue
from backend.agent_framework.graph_store import FrameworkGraph,GraphUnavailable
from backend.agent_framework.graph_projection import ProjectionWorker,mark_project_changed,project_snapshot
from tests.app.test_db_integration import database
from tests.app.test_v1_auth_integration import auth_database,csrf
from tests.agent_framework.test_pdf_postgres_integration import document_database as initial_pdf_database,runtime,upload
from tests.agent_framework.test_criteria_postgres_integration import document_database
from tests.agent_framework.test_a02_postgres_integration import a02,register,admit
from tests.agent_framework.test_poster_postgres_integration import pipeline,image,create_review,approve,run_worker

pytestmark=pytest.mark.db_integration


@pytest.fixture(scope='module')
def graph_target():
    from tests.agent_framework.graph_test_environment import isolated_graph_settings,UnsafeGraphTestTarget
    try:return isolated_graph_settings()
    except UnsafeGraphTestTarget as error:pytest.fail(str(error),pytrace=False)


@pytest.fixture(scope='module',autouse=True)
def graph_database(graph_target,document_database):
    auth,settings=document_database
    assert migrate(settings,target='AF005')['revision']=='AF005'
    with unit_of_work(auth[0].engine) as c:
        assert check_extension(c)['revision']=='AF005'
        check_app_permissions(c)
    yield


@pytest.fixture
def rag(pipeline,graph_target):
    r=pipeline
    graph=FrameworkGraph(graph_target['uri'],'neo4j',graph_target['password'])
    graph.initialize()
    config=TeamConfiguration.model_validate(r.config.model_dump(mode='json')|{'context_preparation':{'mode':'neo4j_rag'}})
    r.config=config;r.store=PostgresStore(r.db.engine,config,r.app.state.authorization)
    r.app.state.framework=r.store
    r.app.state.criteria_review=PostgresCriteriaReview(r.store,r.app.state.documents)
    r.app.state.poster_reviews=PostgresPosterReviews(r.store,r.app.state.criteria_review,r.app.state.poster_reviews.storage)
    r.queue=PostgresQueue(r.store,graph=graph);r.graph=graph;r.projection=ProjectionWorker(r.db.engine,graph)
    try:
        yield r
    finally:
        # This UUID belongs to the disposable test DB. No other graph is touched.
        with graph._session() as s:
            s.run('MATCH(n:AFEntity {project_id:$p}) DETACH DELETE n',p=str(r.pid)).consume()
            s.run('MATCH(n:AFProjection {project_id:$p}) DELETE n',p=str(r.pid)).consume()
        graph.close()


def admitted(r):
    snapshot=create_review(r,image(r,'normal'))
    assert approve(r,snapshot,'approve_criteria').status_code==200
    return snapshot['context']['run_id']


def synced(r):
    for _ in range(50):
        if not r.projection.deliver_one(project_id=r.pid):return
    raise AssertionError('Projection did not become idle')


def test_actual_graph_feeds_one_context_to_inspector_and_reporter(rag,monkeypatch):
    r=rag;rid=admitted(r);synced(r);seen={}
    def capture(agents):
        for role,method in [('inspector','inspect'),('reporter','report')]:
            cls=type(getattr(agents,role));original=getattr(cls,method)
            def call(self,request,role=role,original=original):
                seen[role]=request.evidence
                return original(self,request)
            monkeypatch.setattr(cls,method,call)
    result,outcome,_=run_worker(r,capture)
    assert outcome=='completed'
    assert seen['inspector']==seen['reporter']
    response=r.client.get('/api/framework/v1/runs/'+rid+'/evidence-context')
    assert response.status_code==200,response.text
    context=response.json()
    assert context['retrieval']['backend']=='neo4j' and context['retrieval']['fallback_reason'] is None
    assert context['sha256']==digest(result['evidence'])
    assert len(result['evidence']['evidence'])==len(result['criteria']['criteria'])==3
    assert result['synthetic'] and result['evidence']['synthetic']
    assert [s['role'] for s in result['step_records']]==['A02','A04_A05','A06','A07']
    assert r.client.get('/api/framework/v1/projects/'+str(r.pid)+'/criteria-impact',params={
        'criteria_version_id':r.baseline['version']['criteria_version_id'],'criterion_id':'c001'}).status_code==200


@pytest.mark.parametrize('fault',['offline','stale','forged'])
def test_equivalent_sql_fallback_preserves_all_approved_sources(rag,fault):
    r=rag;rid=admitted(r);synced(r);real=r.graph
    class Fault:
        def approved_sources(self,*args):
            if fault=='offline':raise GraphUnavailable()
            value=real.approved_sources(*args)
            if fault=='stale':value['revision']-=1
            else:value['sources'][0]['source_hash']='0'*64
            return value
    r.queue.graph=Fault()
    result,outcome,_=run_worker(r)
    assert outcome=='completed'
    trace=r.client.get('/api/framework/v1/runs/'+rid+'/evidence-context').json()['retrieval']
    assert trace['backend']=='postgresql' and trace['fallback_reason']
    assert result['evidence']['graph_revision'] is None
    from backend.agent_framework.context import freeze_approved_context
    from backend.agent_framework.contracts import RunSnapshot
    snap=RunSnapshot.model_validate(result)
    assert snap.evidence.evidence==freeze_approved_context(snap.context,snap.criteria.criteria,synthetic=True).evidence


def test_replay_old_revision_and_rebuild_keep_exact_scoped_sources(rag):
    r=rag;admitted(r);synced(r)
    with r.db.engine.connect() as c:
        nodes,edges=project_snapshot(c,r.pid)
        revision=c.execute(text('SELECT revision FROM agent_framework.graph_state WHERE project_id=:p'),{'p':r.pid}).scalar_one()
    assert r.graph.replace_project(str(r.pid),nodes,edges,revision) is False
    assert r.graph.replace_project(str(r.pid),[],[],revision-1) is False
    before=r.graph.approved_sources(str(r.pid),r.baseline['version']['criteria_version_id'])
    with unit_of_work(r.db.engine) as c:mark_project_changed(c,r.pid,'graph.rebuild_requested',r.pid)
    synced(r)
    after=r.graph.approved_sources(str(r.pid),r.baseline['version']['criteria_version_id'])
    assert before['sources']==after['sources'] and after['revision']>before['revision']


def test_new_event_during_graph_io_is_not_acknowledged_as_delivered(rag):
    r=rag;admitted(r)
    class Concurrent:
        def replace_project(self,*args):
            with unit_of_work(r.db.engine) as c:mark_project_changed(c,r.pid,'concurrent.change',r.pid)
            return r.graph.replace_project(*args)
    assert ProjectionWorker(r.db.engine,Concurrent()).deliver_one(project_id=r.pid)
    with r.db.engine.connect() as c:
        row=c.execute(text('SELECT revision,applied_revision FROM agent_framework.graph_state WHERE project_id=:p'),{'p':r.pid}).one()
        assert row.revision>row.applied_revision
        assert c.execute(text('SELECT count(*) FROM agent_framework.graph_outbox WHERE project_id=:p AND processed_at IS NULL'),{'p':r.pid}).scalar_one()>0
    synced(r)


def test_context_and_history_are_project_authorized_and_drafts_are_not_prepared(rag):
    r=rag
    assert r.client.get('/api/framework/v1/runs/'+r.source_id+'/evidence-context').status_code==409
    assert r.client.get('/api/framework/v1/projects/'+str(uuid4())+'/graph-status').status_code in (403,404)
    assert r.client.get('/api/framework/v1/projects/'+str(r.pid)+'/criteria-impact',params={
        'criteria_version_id':r.baseline['version']['criteria_version_id'],'criterion_id':'not-in-this-version'}).status_code==404



def test_graph_commit_before_failed_ack_replays_without_duplicate_sources(rag):
    r=rag;admitted(r)
    class LostAck:
        def replace_project(self,*args):
            r.graph.replace_project(*args)
            raise ConnectionError('scripted lost acknowledgement')
    worker=ProjectionWorker(r.db.engine,LostAck())
    assert worker.deliver_one(project_id=r.pid) and worker.last_outcome=='deferred'
    before=r.graph.approved_sources(str(r.pid),r.baseline['version']['criteria_version_id'])
    with unit_of_work(r.db.engine) as c:
        c.execute(text('UPDATE agent_framework.graph_state SET available_at=clock_timestamp() WHERE project_id=:p'),{'p':r.pid})
    synced(r)
    after=r.graph.approved_sources(str(r.pid),r.baseline['version']['criteria_version_id'])
    assert after==before
    status=r.client.get('/api/framework/v1/projects/'+str(r.pid)+'/graph-status').json()
    assert status['status']=='synced'


def test_context_record_is_immutable_and_reloaded_after_worker_replacement(rag):
    r=rag;rid=admitted(r);synced(r)
    result,outcome,_=run_worker(r)
    assert outcome=='completed'
    response=r.client.get('/api/framework/v1/runs/'+rid+'/evidence-context').json()
    from backend.agent_framework.evidence_context import read_context
    fresh=PostgresStore(r.db.engine,r.config,r.app.state.authorization)
    assert read_context(fresh,r.subject,rid)==response
    from backend.app.persistence.database import PersistenceError
    with pytest.raises(PersistenceError):
        with unit_of_work(r.db.engine) as c:
            c.execute(text('UPDATE agent_framework.evidence_contexts SET context_sha256=:h WHERE run_id=:r'),{'h':'0'*64,'r':rid})


def test_permission_rechecked_after_graph_read_before_any_model_call(rag):
    r=rag;rid=admitted(r);synced(r)
    class Revoked:
        def approved_sources(self,*args):
            result=r.graph.approved_sources(*args)
            with unit_of_work(r.db.engine) as c:
                c.execute(text("UPDATE public.projects SET business_state='archived',archived_at=clock_timestamp() WHERE project_id=:p"),{'p':r.pid})
            return result
    r.queue.graph=Revoked()
    result,outcome,_=run_worker(r)
    assert outcome in ('authority_lost','failed')
    assert not any(s['role']=='A04_A05' and s['status']=='completed' for s in result['step_records'])
    with r.db.engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM agent_framework.evidence_contexts WHERE run_id=:r'),{'r':rid}).scalar_one()==0


def test_driver_connection_failure_uses_equivalent_sql_sources(rag):
    import socket
    r=rag;rid=admitted(r);synced(r)
    # A bound, non-listening socket reserves a port that refuses real driver I/O.
    with socket.socket() as closed:
        closed.bind(('127.0.0.1',0))
        unavailable=FrameworkGraph('bolt://127.0.0.1:'+str(closed.getsockname()[1]),'neo4j','synthetic-test-only')
        r.queue.graph=unavailable
        try:
            result,outcome,_=run_worker(r)
        finally:unavailable.close()
    assert outcome=='completed'
    trace=r.client.get('/api/framework/v1/runs/'+rid+'/evidence-context').json()['retrieval']
    assert trace['backend']=='postgresql' and trace['fallback_reason']=='GRAPH_UNAVAILABLE'
    assert len(result['evidence']['evidence'])==3 and result['evidence']['graph_revision'] is None


def test_file_changed_during_graph_read_blocks_model_callback(rag):
    from uuid import UUID
    from types import SimpleNamespace
    from backend.app.storage.local import key
    r=rag;rid=admitted(r);synced(r);calls=[]
    pdf=r.root/key(r.pid,UUID(r.registered['document']['file_id']))
    original=pdf.read_bytes()
    class ChangedOriginal:
        def approved_sources(self,*args):
            value=r.graph.approved_sources(*args)
            pdf.write_bytes(b'scripted change after graph lookup')
            return value
    r.queue.graph=ChangedOriginal()
    def capture(agents):
        agents.inspector.callback=SimpleNamespace(inspect_poster=lambda *_:calls.append(1))
    try:
        result,outcome,_=run_worker(r,capture)
        assert outcome in ('authority_lost','failed')
        assert result['inspection'] is None and result['machine_verdict'] is None
        assert calls==[]
        # The registered reader records a failed role but never calls its provider.
        assert all(s['status']=='failed' for s in result['step_records'] if s['role']=='A04_A05')
    finally:pdf.write_bytes(original)


def test_mandatory_postgres_source_changed_during_graph_read_is_not_saved_as_evidence(rag):
    r=rag;rid=admitted(r);synced(r)
    class ChangedSource:
        def approved_sources(self,*args):
            value=r.graph.approved_sources(*args)
            with unit_of_work(r.db.engine) as c:
                c.execute(text("UPDATE agent_framework.runs SET snapshot_json=jsonb_set(snapshot_json,"
                    "'{criteria,warnings}',CAST(:w AS jsonb)) WHERE run_id=:r"),
                    {'r':r.source_id,'w':'["scripted source tamper"]'})
            return value
    r.queue.graph=ChangedSource()
    result,outcome,_=run_worker(r)
    assert outcome in ('authority_lost','failed')
    assert result['inspection'] is None and result['machine_verdict'] is None
    assert not any(s['role']=='A04_A05' for s in result['step_records'])
    with r.db.engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM agent_framework.evidence_contexts WHERE run_id=:r'),{'r':rid}).scalar_one()==0


@pytest.mark.parametrize('wrong_password',[False,True])
def test_graph_check_is_authenticated_read_only_and_does_not_initialize(rag,tmp_path,wrong_password):
    import subprocess,sys
    from tests.app.auth_fixtures import auth_environment
    r=rag;synced(r)
    with r.db.engine.connect() as c:
        before=c.execute(text('SELECT revision,applied_revision FROM agent_framework.graph_state WHERE project_id=:p'),{'p':r.pid}).one()
    settings=auth_environment(r.db)|{'CC_NEO4J_URI':r.graph.uri,'CC_NEO4J_USER':'neo4j',
        'CC_NEO4J_PASSWORD':'incorrect-test-password' if wrong_password else r.graph._password}
    path=tmp_path/'check.env';path.write_text(''.join(k+'='+str(v)+'\n' for k,v in settings.items()));path.chmod(0o600)
    result=subprocess.run([sys.executable,'-m','backend.agent_framework','graph-check','--env-file',str(path)],capture_output=True,text=True,timeout=30)
    if wrong_password:
        assert result.returncode==1
        assert 'incorrect-test-password' not in result.stdout+result.stderr
    else:
        assert result.returncode==0,result.stdout
        assert __import__('json').loads(result.stdout)=={'postgres':'ready','neo4j':'ready','schema':'AF005','read_only':True}
    with r.db.engine.connect() as c:
        after=c.execute(text('SELECT revision,applied_revision FROM agent_framework.graph_state WHERE project_id=:p'),{'p':r.pid}).one()
    assert before==after


@pytest.mark.parametrize('condition',['healthy','late','failures','missing','offline'])
def test_read_only_monitor_observes_real_databases_without_repairs(rag,condition):
    from backend.agent_framework.graph_monitor import observe,MonitorPolicy
    r=rag;admitted(r);synced(r);graph=r.graph;policy=MonitorPolicy()
    if condition in ('late','failures'):
        with unit_of_work(r.db.engine) as c:mark_project_changed(c,r.pid,'monitor.synthetic_change',r.pid)
    if condition=='late':
        with r.owner.begin() as c:
            c.execute(text("UPDATE agent_framework.graph_outbox SET created_at=clock_timestamp()-interval '5 minutes' "
                'WHERE project_id=:p AND processed_at IS NULL'),{'p':r.pid})
    if condition=='failures':
        class Unavailable:
            def replace_project(self,*_):raise ConnectionError('synthetic unavailable')
        assert ProjectionWorker(r.db.engine,Unavailable()).deliver_one(project_id=r.pid)
        policy=MonitorPolicy(failure_threshold=1)
    if condition=='missing':
        with graph._session() as s:s.run('MATCH(p:AFProjection {project_id:$p}) DELETE p',p=str(r.pid)).consume()
    if condition=='offline':
        class Offline:
            def projection_revisions(self,*_):raise GraphUnavailable()
        graph=Offline()
    with r.db.engine.connect() as c:
        before=c.execute(text('SELECT revision,applied_revision,attempt_no FROM agent_framework.graph_state WHERE project_id=:p'),{'p':r.pid}).one()
    result=observe(r.db.engine,graph,policy,project_id=r.pid)
    expected={'healthy':None,'late':'SYNC_LAGGING','failures':'CONSECUTIVE_SYNC_FAILURES',
        'missing':'GRAPH_PROJECTION_MISSING','offline':'NEO4J_UNAVAILABLE'}[condition]
    assert (result['state']=='healthy')==(condition=='healthy')
    if expected:assert expected in {a['code'] for a in result['alerts']}
    assert result['read_only'] and not result['automatic_repair']
    with r.db.engine.connect() as c:
        assert c.execute(text('SELECT revision,applied_revision,attempt_no FROM agent_framework.graph_state WHERE project_id=:p'),{'p':r.pid}).one()==before
    if condition=='missing':
        with r.graph._session() as s:
            assert s.run('MATCH(p:AFProjection {project_id:$p}) RETURN count(p) AS n',p=str(r.pid)).single()['n']==0


def test_monitor_process_records_warning_and_recovery_without_repeated_alerts(rag,tmp_path):
    import subprocess,sys,time,json
    from tests.app.auth_fixtures import auth_environment
    r=rag;admitted(r);synced(r)
    settings=auth_environment(r.db)|{'CC_NEO4J_URI':r.graph.uri,'CC_NEO4J_USER':'neo4j','CC_NEO4J_PASSWORD':r.graph._password}
    env=tmp_path/'monitor.env';env.write_text(''.join(k+'='+str(v)+'\n' for k,v in settings.items()));env.chmod(0o600)
    state=tmp_path/'latest.json'
    process=subprocess.Popen([sys.executable,'-m','backend.agent_framework','graph-monitor','--env-file',str(env),
        '--state-file',str(state),'--interval','1','--lag-seconds','1','--project-id',str(r.pid)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def wait_for(expected):
        end=time.monotonic()+20
        while time.monotonic()<end:
            assert process.poll() is None,'Monitor process stopped unexpectedly'
            if state.exists():
                value=json.loads(state.read_text())
                if value['state']==expected:return value
            time.sleep(0.1)
        raise AssertionError('Monitor did not reach expected state')
    try:
        wait_for('healthy')
        with unit_of_work(r.db.engine) as c:mark_project_changed(c,r.pid,'monitor.synthetic_transition',r.pid)
        warning=wait_for('warning')
        assert 'SYNC_LAGGING' in {a['code'] for a in warning['alerts']}
        time.sleep(1.2)
        synced(r);wait_for('healthy')
    finally:
        process.terminate();output,_stderr=process.communicate(timeout=15)
    assert process.returncode==0
    events=[json.loads(line) for line in output.splitlines() if line.startswith('{')]
    assert [e['state'] for e in events if e['event']=='framework_graph_monitor_changed']==['healthy','warning','healthy']


# Only disposable, attested fixtures are damaged in the following recovery tests.
def recovery_business_hash(r):
    tables=('runs','steps','approvals','events','document_versions','criteria_versions','criteria_approval_bindings','evidence_contexts')
    with r.db.engine.connect() as c:
        return {name:digest(sorted((dict(row) for row in c.execute(text('SELECT to_jsonb(t) AS v FROM agent_framework.'+name+' t')).mappings()),key=str))
            for name in tables}


def recovery_count(r):
    with r.db.engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM agent_framework.graph_outbox WHERE project_id=:p AND kind='graph.recovery_requested'"),{'p':r.pid}).scalar_one()


def damage_projection(r,kind):
    with r.graph._session() as s:
        if kind=='missing':
            s.run('MATCH(n:AFEntity {project_id:$p}) DETACH DELETE n',p=str(r.pid)).consume()
            s.run('MATCH(n:AFProjection {project_id:$p}) DELETE n',p=str(r.pid)).consume()
        elif kind=='body':
            s.run('MATCH(n:AFEntity {project_id:$p}) SET n.synthetic_corruption="test"',p=str(r.pid)).consume()
        elif kind=='ahead':
            s.run('MATCH(n:AFProjection {project_id:$p}) SET n.revision=n.revision+10',p=str(r.pid)).consume()


@pytest.mark.parametrize('fault',['missing','body'])
def test_recovery_confirms_rebuilds_and_preserves_approval_source_history(rag,fault):
    from datetime import datetime,timezone,timedelta
    from backend.agent_framework.graph_recovery import Recovery,content_hash
    r=rag;admitted(r);synced(r);business=recovery_business_hash(r)
    before=r.graph.read_projection(str(r.pid));damage_projection(r,fault)
    recovery=Recovery(r.db.engine,r.graph);now=datetime.now(timezone.utc)
    first,candidate=recovery.inspect(r.pid,now=now)
    assert first['status']=='observing' and recovery_count(r)==0
    second,_=recovery.inspect(r.pid,candidate,now=now+timedelta(seconds=29))
    assert second['status']=='observing' and recovery_count(r)==0
    result,_=recovery.inspect(r.pid,candidate,now=now+timedelta(seconds=31))
    assert result['status']=='requested',result
    duplicate,_=recovery.inspect(r.pid,candidate,now=now+timedelta(seconds=32))
    assert duplicate['status'] in ('pending','observing') and recovery_count(r)==1
    synced(r);after=r.graph.read_projection(str(r.pid))
    assert after['revision']>before['revision'] and content_hash(after['nodes'],after['edges'])==content_hash(before['nodes'],before['edges'])
    assert recovery.inspect(r.pid)[0]['status']=='consistent'
    assert recovery_business_hash(r)==business
    damage_projection(r,'body')
    _,candidate=recovery.inspect(r.pid,now=now)
    cooldown,_=recovery.inspect(r.pid,candidate,now=now+timedelta(seconds=31))
    assert cooldown['status']=='cooldown' and recovery_count(r)==1
    from backend.agent_framework.graph_recovery import RecoveryPolicy
    limited,_=Recovery(r.db.engine,r.graph,RecoveryPolicy(max_per_hour=1)).inspect(r.pid,candidate,now=now+timedelta(seconds=32))
    assert limited['reason']=='RECOVERY_RATE_LIMIT' and recovery_count(r)==1


@pytest.mark.parametrize('fault',['ahead','unmanaged','source'])
def test_recovery_refuses_unsafe_overwrite_or_untrusted_authority(rag,fault):
    from datetime import datetime,timezone,timedelta
    from backend.agent_framework.graph_recovery import Recovery
    r=rag;admitted(r);synced(r);now=datetime.now(timezone.utc);recovery=Recovery(r.db.engine,r.graph)
    if fault=='ahead':damage_projection(r,'ahead')
    elif fault=='unmanaged':
        with r.graph._session() as s:
            s.run('MATCH(n:AFEntity {project_id:$p}) WITH n LIMIT 1 CREATE (x:ExternalRecoveryTest {marker:$p}) CREATE(n)-[:KEEP_ME]->(x)',p=str(r.pid)).consume()
        try:
            with r.db.engine.connect() as c:nodes,edges=project_snapshot(c,r.pid)
            with pytest.raises(GraphUnavailable,match='unavailable'):
                r.graph.replace_project(str(r.pid),nodes,edges,100000)
        finally:
            with r.graph._session() as s:
                assert s.run('MATCH(:AFEntity {project_id:$p})-[:KEEP_ME]->(x) RETURN count(x) AS n',p=str(r.pid)).single()['n']==1
    else:
        damage_projection(r,'body')
        with unit_of_work(r.owner) as c:
            c.execute(text("UPDATE agent_framework.runs SET input_sha256=:hash WHERE run_id=:r"),{'hash':'0'*64,'r':r.source_id})
    try:
        first,candidate=recovery.inspect(r.pid,now=now)
        result,_=recovery.inspect(r.pid,candidate,now=now+timedelta(seconds=31))
        assert result['status']=='manual_review',result
        assert recovery_count(r)==0
    finally:
        if fault=='unmanaged':
            with r.graph._session() as s:s.run('MATCH(x:ExternalRecoveryTest {marker:$p}) DETACH DELETE x',p=str(r.pid)).consume()


def test_normal_pending_delivery_and_graph_outage_never_enqueue_repair(rag):
    from backend.agent_framework.graph_recovery import Recovery
    r=rag;admitted(r);synced(r)
    with unit_of_work(r.db.engine) as c:mark_project_changed(c,r.pid,'test.pending',r.pid)
    assert Recovery(r.db.engine,r.graph).inspect(r.pid)[0]['status']=='pending'
    class Offline:
        def read_projection(self,*args):raise GraphUnavailable()
    assert Recovery(r.db.engine,Offline()).inspect(r.pid)[0]['status']=='unavailable'
    assert recovery_count(r)==0


def test_confirmed_candidate_is_rechecked_after_graph_io(rag):
    from datetime import datetime,timezone,timedelta
    from backend.agent_framework.graph_recovery import Recovery
    r=rag;admitted(r);synced(r);damage_projection(r,'body');now=datetime.now(timezone.utc)
    recovery=Recovery(r.db.engine,r.graph);_,candidate=recovery.inspect(r.pid,now=now)
    class Concurrent:
        def read_projection(self,*args):
            value=r.graph.read_projection(*args)
            with unit_of_work(r.db.engine) as c:mark_project_changed(c,r.pid,'test.concurrent_change',r.pid)
            return value
    result,_=Recovery(r.db.engine,Concurrent()).inspect(r.pid,candidate,now=now+timedelta(seconds=31))
    assert result['reason']=='AUTHORITY_CHANGED' and recovery_count(r)==0


def test_mismatched_same_revision_is_not_acknowledged_as_projected(rag):
    r=rag;admitted(r)
    class LostAck:
        def replace_project(self,*args):
            r.graph.replace_project(*args);damage_projection(r,'body')
            raise ConnectionError('scripted lost ack')
    assert ProjectionWorker(r.db.engine,LostAck()).deliver_one(project_id=r.pid)
    with unit_of_work(r.db.engine) as c:c.execute(text('UPDATE agent_framework.graph_state SET available_at=clock_timestamp() WHERE project_id=:p'),{'p':r.pid})
    assert r.projection.deliver_one(project_id=r.pid) and r.projection.last_outcome=='deferred'
    with r.db.engine.connect() as c:
        row=c.execute(text('SELECT revision,applied_revision FROM agent_framework.graph_state WHERE project_id=:p'),{'p':r.pid}).one()
        assert row.revision>row.applied_revision


def test_recovery_process_restarts_from_persisted_confirmation_without_duplicate_request(rag,tmp_path):
    import json,subprocess,sys,time
    from datetime import datetime,timezone,timedelta
    from tests.app.auth_fixtures import auth_environment
    from backend.agent_framework.graph_monitor import save_snapshot
    r=rag;admitted(r);synced(r);damage_projection(r,'missing')
    settings=auth_environment(r.db)|{'CC_NEO4J_URI':r.graph.uri,'CC_NEO4J_USER':'neo4j','CC_NEO4J_PASSWORD':r.graph._password}
    env=tmp_path/'recovery.env';env.write_text(''.join(k+'='+str(v)+'\n' for k,v in settings.items()));env.chmod(0o600)
    state=tmp_path/'latest.json'
    def start():
        return subprocess.Popen([sys.executable,'-m','backend.agent_framework','graph-recovery','--env-file',str(env),
            '--state-file',str(state),'--project-id',str(r.pid)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    def wait_for(process,expected):
        end=time.monotonic()+20
        while time.monotonic()<end:
            assert process.poll() is None,'Recovery process stopped unexpectedly'
            if state.exists():
                value=json.loads(state.read_text())
                if any(a['status']==expected for a in value.get('actions',[])):return value
            time.sleep(0.1)
        raise AssertionError('Recovery process did not reach '+expected)
    def stop(process):
        process.terminate();output,error=process.communicate(timeout=15)
        assert process.returncode==0,error
        return output
    first=subprocess.run([sys.executable,'-m','backend.agent_framework','graph-recovery','--env-file',str(env),
        '--state-file',str(state),'--project-id',str(r.pid),'--once'],capture_output=True,text=True,timeout=20)
    assert first.returncode==1,first.stderr
    value=json.loads(state.read_text())
    assert value['actions'][0]['status']=='observing' and recovery_count(r)==0
    # Simulate confirmation age in this test-owned operational file, then restart the real CLI.
    value['candidates'][str(r.pid)]['since']=(datetime.now(timezone.utc)-timedelta(seconds=31)).isoformat()
    save_snapshot(state,value);process=start()
    try:wait_for(process,'requested')
    finally:stop(process)
    assert recovery_count(r)==1;synced(r);process=start()
    try:
        value=wait_for(process,'consistent');assert value['state']=='healthy'
    finally:stop(process)
    assert recovery_count(r)==1


def test_scoped_recovery_leaves_unknown_existing_graph_project_untouched(rag):
    from datetime import datetime,timezone,timedelta
    from backend.agent_framework.graph_recovery import Recovery
    r=rag;admitted(r);synced(r);foreign=str(uuid4())
    r.graph.replace_project(foreign,[{'key':'kept','kind':'historical','sha256':'f'*64}],[],7)
    before=r.graph.read_projection(foreign)
    try:
        damage_projection(r,'missing');now=datetime.now(timezone.utc);recovery=Recovery(r.db.engine,r.graph)
        _,candidate=recovery.inspect(r.pid,now=now)
        assert recovery.inspect(r.pid,candidate,now=now+timedelta(seconds=31))[0]['status']=='requested'
        synced(r);assert r.graph.read_projection(foreign)==before
    finally:
        with r.graph._session() as s:
            s.run('MATCH(n:AFEntity {project_id:$p}) DETACH DELETE n',p=foreign).consume()
            s.run('MATCH(n:AFProjection {project_id:$p}) DELETE n',p=foreign).consume()
