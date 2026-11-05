"""Conservative, project-scoped recovery requests. PG business history stays sealed.

Confirm the same anomaly twice, revalidate PG under its row lock, then enqueue an
auditable AF005 outbox revision. The existing projection worker does the rebuild.
No PG transaction is held during Neo4j I/O, and unmanaged graph links are refused.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
import json
from pathlib import Path
import signal
from threading import Event
from uuid import UUID

from sqlalchemy import text
from backend.app.persistence.database import create_app_engine
from backend.app.persistence.transaction import unit_of_work
from .postgres import digest
from .graph_projection import project_snapshot,mark_project_changed
from .graph_store import FrameworkGraph,GraphUnavailable
from .graph_monitor import save_snapshot,load_snapshot
from .postgres_commands import _values,_ready

FORMAT='af-graph-recovery-v1'


@dataclass(frozen=True)
class RecoveryPolicy:
    confirmation_seconds:int=30
    cooldown_seconds:int=300
    max_per_hour:int=3
    batch_size:int=5

    def __post_init__(self):
        if any(type(v) is not int or v<1 for v in (self.confirmation_seconds,self.cooldown_seconds,self.max_per_hour,self.batch_size)):
            raise ValueError('Invalid recovery policy')


def content_hash(nodes,edges):
    return digest({'nodes':sorted(nodes,key=lambda n:n['key']),
        'edges':sorted(edges,key=lambda e:(e['source'],e['target'],e['kind']))})


def validate_source(c,pid):
    """Only repair from internally consistent registered-document history."""
    docs={str(r['document_version_id']):r for r in c.execute(text('SELECT document_version_id,sha256 FROM '
        'agent_framework.document_versions WHERE project_id=:p'),{'p':pid}).mappings()}
    runs={r['run_id']:r for r in c.execute(text('SELECT run_id,input_json,input_sha256,snapshot_json FROM '
        'agent_framework.runs WHERE project_id=:p'),{'p':pid}).mappings()}
    for row in runs.values():
        raw=row['input_json'];context=row['snapshot_json'].get('context',{});doc=raw.get('document') or {}
        if (digest(raw)!=row['input_sha256'] or context.get('project_id')!=str(pid) or context.get('run_id')!=row['run_id']
                or doc.get('document_version_id') not in docs or docs[doc['document_version_id']]['sha256']!=doc.get('sha256')):
            raise ValueError('SOURCE_INTEGRITY_INVALID')
    for row in c.execute(text('SELECT s.output_json,s.output_sha256 FROM agent_framework.steps s '
            'JOIN agent_framework.runs r ON r.run_id=s.run_id WHERE r.project_id=:p'),{'p':pid}).mappings():
        if row['output_json'] is not None and digest(row['output_json'])!=row['output_sha256']:
            raise ValueError('SOURCE_INTEGRITY_INVALID')
    versions={str(r['criteria_version_id']):r for r in c.execute(text('SELECT * FROM agent_framework.criteria_versions '
        'WHERE project_id=:p'),{'p':pid}).mappings()}
    for vid,row in versions.items():
        raw=row['snapshot_json']
        if (digest(raw)!=row['snapshot_sha256'] or digest({'actor_id':str(row['created_by']),'request':row['request_json']})!=row['request_sha256']
                or raw.get('criteria_version_id')!=vid or raw.get('source_run_id')!=row['source_run_id']
                or raw.get('document_sha256')!=docs[str(row['document_version_id'])]['sha256']):
            raise ValueError('SOURCE_INTEGRITY_INVALID')
    for row in c.execute(text('SELECT payload_json,payload_sha256 FROM agent_framework.approvals WHERE project_id=:p'),{'p':pid}).mappings():
        if digest(row['payload_json'])!=row['payload_sha256']:raise ValueError('SOURCE_INTEGRITY_INVALID')
    for row in c.execute(text('SELECT b.*,a.run_id AS approval_run,a.project_id AS approval_project,a.actor_id AS approval_actor,a.payload_json AS approval_payload,a.decision '
            'FROM agent_framework.criteria_approval_bindings b JOIN agent_framework.approvals a '
            'ON a.approval_id=b.approval_id WHERE b.project_id=:p'),{'p':pid}).mappings():
        if (row['criteria_sha256']!=versions[str(row['criteria_version_id'])]['snapshot_sha256']
                or digest({'actor_id':str(row['approval_actor']),'request':row['request_json']})!=row['request_sha256'] or row['approval_project']!=pid
                or row['approval_run']!=row['source_run_id'] or row['decision']!='approve_criteria'
                or row['approval_payload'].get('approved_criteria_version_id')!=str(row['criteria_version_id'])
                or row['approval_payload'].get('criteria_sha256')!=row['criteria_sha256']):
            raise ValueError('SOURCE_INTEGRITY_INVALID')
    for row in c.execute(text('SELECT e.*,r.input_sha256 AS run_input FROM agent_framework.evidence_contexts e '
            'JOIN agent_framework.runs r ON r.run_id=e.run_id WHERE r.project_id=:p'),{'p':pid}).mappings():
        if digest(row['context_json'])!=row['context_sha256'] or row['input_sha256']!=row['run_input']:
            raise ValueError('SOURCE_INTEGRITY_INVALID')


def authority(c,pid,*,lock=False):
    row=c.execute(text('SELECT revision,applied_revision,lease_expires_at>clock_timestamp() AS busy '
        'FROM agent_framework.graph_state WHERE project_id=:p'+(' FOR UPDATE' if lock else '')),{'p':pid}).mappings().one_or_none()
    if row is None:return None
    nodes,edges=project_snapshot(c,pid)
    return dict(row)|{'hash':content_hash(nodes,edges)}


def eligible(pg,observed):
    if observed['unmanaged']:return 'manual_review','UNMANAGED_GRAPH_RELATIONSHIPS'
    revision=observed['revision']
    if revision is not None and revision>pg['revision']:return 'manual_review','GRAPH_AHEAD_OF_AUTHORITY'
    same=content_hash(observed['nodes'],observed['edges'])==pg['hash']
    if revision==pg['revision'] and same:return 'consistent','MATCHED'
    if pg['revision']>pg['applied_revision'] and revision!=pg['revision']:
        return 'pending','NORMAL_DELIVERY_PENDING'
    return 'observing','PROJECTION_MISSING' if revision is None else 'PROJECTION_MISMATCH'


class Recovery:
    def __init__(self,engine,graph,policy=None):self.engine,self.graph,self.policy=engine,graph,policy or RecoveryPolicy()

    def inspect(self,project_id,previous=None,*,now=None):
        now=now or datetime.now(timezone.utc);pid=UUID(str(project_id));previous=previous or {}
        action={'project_id':str(pid)}
        with self.engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
            with c.begin():
                c.execute(text('SET TRANSACTION READ ONLY'));pg=authority(c,pid)
        if pg is None:return action|{'status':'manual_review','reason':'UNKNOWN_PROJECT'},None
        if pg['busy']:return action|{'status':'pending','reason':'PROJECTION_LEASE_ACTIVE'},None
        try:observed=self.graph.read_projection(str(pid))
        except GraphUnavailable as error:return action|{'status':'unavailable','reason':error.code},None
        status,reason=eligible(pg,observed)
        if status!='observing':return action|{'status':status,'reason':reason},None
        fingerprint=digest({'revision':pg['revision'],'expected':pg['hash'],
            'observed':content_hash(observed['nodes'],observed['edges']),'graph_revision':observed['revision']})
        candidate={'fingerprint':fingerprint,'since':now.isoformat()}
        if previous.get('fingerprint')!=fingerprint:return action|{'status':status,'reason':reason},candidate
        try:age=(now-datetime.fromisoformat(previous['since'])).total_seconds()
        except (ValueError,KeyError,TypeError):age=-1
        if age<self.policy.confirmation_seconds:return action|{'status':'observing','reason':reason},previous if age>=0 else candidate
        # Recheck authority after graph I/O. The lock also serializes recovery processes.
        with unit_of_work(self.engine) as c:
            current=authority(c,pid,lock=True)
            if current is None or current['revision']!=pg['revision'] or current['applied_revision']!=pg['applied_revision'] or current['hash']!=pg['hash'] or current['busy']:
                return action|{'status':'pending','reason':'AUTHORITY_CHANGED'},None
            budget=c.execute(text("SELECT count(*) FILTER(WHERE created_at>clock_timestamp()-interval '1 hour') AS count,"
                "EXTRACT(EPOCH FROM (clock_timestamp()-max(created_at))) AS elapsed FROM agent_framework.graph_outbox "
                "WHERE project_id=:p AND kind='graph.recovery_requested'"),{'p':pid}).mappings().one()
            if budget['count']>=self.policy.max_per_hour:return action|{'status':'manual_review','reason':'RECOVERY_RATE_LIMIT'},previous
            if budget['elapsed'] is not None and float(budget['elapsed'])<self.policy.cooldown_seconds:
                return action|{'status':'cooldown','reason':'RECOVERY_COOLDOWN'},previous
            try:validate_source(c,pid)
            except (ValueError,KeyError,TypeError):return action|{'status':'manual_review','reason':'SOURCE_INTEGRITY_INVALID'},None
            record={'project_id':str(pid),'reason':reason,'confirmed_revision':pg['revision'],
                'expected_sha256':pg['hash'],'observed_sha256':content_hash(observed['nodes'],observed['edges'])}
            mark_project_changed(c,pid,'graph.recovery_requested',json.dumps(record,sort_keys=True))
        return action|{'status':'requested','reason':reason,'previous_revision':pg['revision']},None

    def cycle(self,previous=None,*,now=None,project_id=None):
        previous=previous or {};now=now or datetime.now(timezone.utc)
        # Keyset scan across every known PG project. Unknown Neo4j projects are never enumerated.
        cursor=previous.get('cursor')
        try:cursor=UUID(cursor) if cursor else UUID(int=0)
        except (ValueError,TypeError):cursor=UUID(int=0)
        with self.engine.connect() as c:
            if project_id is not None:
                selected=[str(p) for p in c.execute(text('SELECT project_id FROM agent_framework.graph_state WHERE project_id=:p'),{'p':UUID(str(project_id))}).scalars()]
            else:
                selected=[str(p) for p in c.execute(text('SELECT project_id FROM agent_framework.graph_state '
                    'WHERE project_id>:cursor ORDER BY project_id LIMIT :limit'),{'cursor':cursor,'limit':self.policy.batch_size}).scalars()]
                if not selected:
                    selected=[str(p) for p in c.execute(text('SELECT project_id FROM agent_framework.graph_state '
                        'ORDER BY project_id LIMIT :limit'),{'limit':self.policy.batch_size}).scalars()]
        candidates=dict(previous.get('candidates',{}));issues=dict(previous.get('issues',{}));actions=[]
        for pid in selected:
            try:action,candidate=self.inspect(pid,candidates.get(pid),now=now)
            except Exception:action,candidate={'project_id':pid,'status':'unavailable','reason':'RECOVERY_CHECK_UNAVAILABLE'},None
            actions.append(action)
            if candidate:candidates[pid]=candidate
            else:candidates.pop(pid,None)
            if action['status'] in ('consistent','pending'):issues.pop(pid,None)
            else:issues[pid]=action
        state='critical' if any(a['status']=='manual_review' for a in issues.values()) else 'warning' if issues else 'healthy'
        return {'format':FORMAT,'observed_at':now.isoformat(),'state':state,'automatic_repair':True,
            'policy':self.policy.__dict__,'actions':actions,'issues':issues,'candidates':candidates,
            'cursor':selected[-1] if selected else '', 'truncated':False}


def recovery_command(args):
    values,settings=_values(args.env_file,'framework-projection');engine=create_app_engine(settings)
    graph=FrameworkGraph(values.get('CC_NEO4J_URI',''),values.get('CC_NEO4J_USER','neo4j'),values.get('CC_NEO4J_PASSWORD',''))
    stop=Event();previous={};signature=None
    try:
        if _ready(engine,settings)['revision']!='AF005':raise ValueError('AF005 required')
        if args.state_file:previous,_=load_snapshot(args.state_file,max_age=86400,expected_format=FORMAT)
        for name in (signal.SIGINT,signal.SIGTERM):signal.signal(name,lambda *_:stop.set())
        recovery=Recovery(engine,graph)
        while not stop.is_set():
            try:value=recovery.cycle(previous,project_id=args.project_id)
            except Exception:value={'format':FORMAT,'observed_at':datetime.now(timezone.utc).isoformat(),
                'state':'critical','automatic_repair':True,'reason':'RECOVERY_AUTHORITY_UNAVAILABLE',
                'candidates':previous.get('candidates',{}),'issues':previous.get('issues',{})}
            if args.state_file:save_snapshot(args.state_file,value)
            if args.once:
                print(json.dumps(value),flush=True);return 0 if value['state']=='healthy' else 1
            new=json.dumps([value['state'],value.get('issues'),value.get('reason')],sort_keys=True)
            if new!=signature:
                print(json.dumps({'event':'framework_graph_recovery_changed','state':value['state'],
                    'actions':value.get('actions',[]),'reason':value.get('reason')}),flush=True);signature=new
            previous=value;stop.wait(15)
        return 0
    finally:graph.close();engine.dispose()


def recovery_status_command(args):
    value,fresh=load_snapshot(args.state_file,max_age=args.max_age,expected_format=FORMAT)
    print(json.dumps(value),flush=True);return 0 if fresh else 1
