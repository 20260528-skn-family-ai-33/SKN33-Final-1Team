"""Read-only projection observations and local state-change logs.

No graph repair, schema migration, model retry, approval or external notification.
The only persistent writes are the monitor's own atomic JSON snapshot.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import signal
from threading import Event
from time import monotonic
from uuid import UUID,uuid4

from sqlalchemy import text
from backend.app.persistence.database import create_app_engine
from .graph_store import FrameworkGraph,GraphUnavailable
from .postgres_commands import _values,_ready
from .postgres_schema import GRAPH_REVISIONS

FORMAT='af-graph-monitor-v1'


@dataclass(frozen=True)
class MonitorPolicy:
    lag_seconds:int=120
    failure_threshold:int=3
    interval_seconds:int=15

    def __post_init__(self):
        if any(type(v) is not int or not 1<=v<=3600 for v in (self.lag_seconds,self.failure_threshold,self.interval_seconds)):
            raise ValueError('Invalid monitor policy')


def read_metrics(engine,*,project_id=None):
    params={'project':UUID(str(project_id))} if project_id is not None else {}
    scope='WHERE s.project_id=:project' if project_id is not None else ''
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
        with c.begin():
            c.execute(text('SET TRANSACTION READ ONLY'))
            rows=c.execute(text('SELECT s.project_id,s.revision,s.applied_revision,s.attempt_no,'
                's.error_json IS NOT NULL AS has_error, '
                '(SELECT count(*) FROM agent_framework.graph_outbox o WHERE o.project_id=s.project_id '
                'AND o.processed_at IS NULL) AS pending_events, '
                '(SELECT GREATEST(0,EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-min(o.created_at)))) '
                'FROM agent_framework.graph_outbox o WHERE o.project_id=s.project_id '
                'AND o.processed_at IS NULL) AS oldest_pending_seconds '
                'FROM agent_framework.graph_state s '+scope+' ORDER BY s.project_id LIMIT 501'),params).mappings().all()
            fallback_scope='AND r.project_id=:project' if project_id is not None else ''
            fallbacks=c.execute(text("SELECT count(*) AS total,count(*) FILTER(WHERE e.trace_json->>'backend'='postgresql') AS fallbacks "
                "FROM agent_framework.evidence_contexts e JOIN agent_framework.runs r ON r.run_id=e.run_id "
                "WHERE e.created_at>=CURRENT_TIMESTAMP-interval '15 minutes' "+fallback_scope),params).one()
    projects=[{'project_id':str(r['project_id']),'required_revision':r['revision'],
        'applied_revision':r['applied_revision'],'consecutive_failures':r['attempt_no'],
        'has_error':r['has_error'],'pending_events':r['pending_events'],
        'oldest_pending_seconds':round(float(r['oldest_pending_seconds'] or 0),3)} for r in rows[:500]]
    return {'projects':projects,'truncated':len(rows)>500,
        'recent_contexts':fallbacks.total,'recent_sql_fallbacks':fallbacks.fallbacks}


def classify(metrics,revisions,policy,*,graph_available=True):
    alerts=[]
    def alert(code,severity,project=None):
        item={'code':code,'severity':severity}
        if project is not None:item['project_id']=project['project_id']
        alerts.append(item)
    if not graph_available:alert('NEO4J_UNAVAILABLE','critical')
    if metrics['truncated']:alert('MONITOR_SCOPE_TRUNCATED','warning')
    for p in metrics['projects']:
        pending=p['required_revision']>p['applied_revision'] or p['pending_events']>0
        if p['consecutive_failures']>=policy.failure_threshold:alert('CONSECUTIVE_SYNC_FAILURES','critical',p)
        if pending and p['oldest_pending_seconds']>=policy.lag_seconds:alert('SYNC_LAGGING','warning',p)
        if pending and not p['pending_events']:alert('PENDING_REVISION_WITHOUT_OUTBOX','critical',p)
        if graph_available:
            revision=revisions.get(p['project_id'])
            p['observed_graph_revision']=revision
            if p['applied_revision']>0 and revision is None:alert('GRAPH_PROJECTION_MISSING','critical',p)
            elif revision is not None and revision<p['applied_revision']:alert('GRAPH_BEHIND_CONFIRMED_REVISION','critical',p)
            # A newer graph can be a normal concurrent delivery after our PG snapshot.
    if metrics['recent_sql_fallbacks']>=3 and metrics['recent_sql_fallbacks']/max(1,metrics['recent_contexts'])>=0.5:
        alert('FREQUENT_SQL_FALLBACK','warning')
    state='critical' if any(a['severity']=='critical' for a in alerts) else 'warning' if alerts else 'healthy'
    return state,sorted(alerts,key=lambda a:(a['code'],a.get('project_id','')))


def observe(engine,graph,policy,*,project_id=None):
    try:
        metrics=read_metrics(engine,project_id=project_id)
    except Exception:
        return snapshot('critical',[{'code':'POSTGRES_UNAVAILABLE','severity':'critical'}],policy,
            {'postgres':'unavailable','neo4j':'not_checked'},None)
    graph_available=True
    try:revisions=graph.projection_revisions([p['project_id'] for p in metrics['projects']])
    except GraphUnavailable:graph_available=False;revisions={}
    state,alerts=classify(metrics,revisions,policy,graph_available=graph_available)
    return snapshot(state,alerts,policy,{'postgres':'ready','neo4j':'ready' if graph_available else 'unavailable'},metrics)


def snapshot(state,alerts,policy,dependencies,metrics):
    return {'format':FORMAT,'observed_at':datetime.now(timezone.utc).isoformat(),'state':state,
        'policy':{'lag_seconds':policy.lag_seconds,'failure_threshold':policy.failure_threshold,
            'interval_seconds':policy.interval_seconds},'dependencies':dependencies,'alerts':alerts,
        'metrics':metrics,'read_only':True,'automatic_repair':False}


def save_snapshot(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False)
        temporary.chmod(0o600);os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def load_snapshot(path,*,max_age=60,now=None,expected_format=FORMAT):
    now=now or datetime.now(timezone.utc)
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(value,dict) or value.get('format')!=expected_format or value.get('state') not in ('healthy','warning','critical'):
            raise ValueError()
        age=(now-datetime.fromisoformat(value['observed_at'])).total_seconds()
        if not 0<=age<=max_age:raise ValueError()
        return value,True
    except (OSError,ValueError,TypeError,KeyError):
        return {'format':expected_format,'state':'unknown','alerts':[{'code':'MONITOR_SNAPSHOT_STALE_OR_MISSING','severity':'critical'}],
            'read_only':True,'automatic_repair':False},False


class Transitions:
    def __init__(self):self.previous=None;self.last_heartbeat=monotonic()
    def event(self,value):
        signature=json.dumps([value['state'],value['alerts']],sort_keys=True)
        if signature!=self.previous:
            self.previous=signature;self.last_heartbeat=monotonic()
            return {'event':'framework_graph_monitor_changed','state':value['state'],
                'alerts':value['alerts'],'observed_at':value['observed_at']}
        if monotonic()-self.last_heartbeat>=300:
            self.last_heartbeat=monotonic()
            return {'event':'framework_graph_monitor_heartbeat','state':value['state'],'observed_at':value['observed_at']}
        return None


def monitor_command(args):
    values,settings=_values(args.env_file,'framework-projection')
    policy=MonitorPolicy(args.lag_seconds,args.failure_threshold,args.interval)
    engine=create_app_engine(settings)
    graph=FrameworkGraph(values.get('CC_NEO4J_URI',''),values.get('CC_NEO4J_USER','neo4j'),values.get('CC_NEO4J_PASSWORD',''))
    stop=Event();transitions=Transitions()
    try:
        # No implicit migration: incompatible schemas fail at startup.
        if _ready(engine,settings)['revision'] not in GRAPH_REVISIONS:raise ValueError('AF005 required')
        for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,lambda *_:stop.set())
        while not stop.is_set():
            value=observe(engine,graph,policy,project_id=args.project_id)
            if args.once:
                print(json.dumps(value),flush=True);return 0 if value['state']=='healthy' else 1
            if args.state_file:save_snapshot(args.state_file,value)
            event=transitions.event(value)
            if event:print(json.dumps(event),flush=True)
            stop.wait(policy.interval_seconds)
        return 0
    finally:graph.close();engine.dispose()


def monitor_status_command(args):
    value,fresh=load_snapshot(args.state_file,max_age=args.max_age)
    print(json.dumps(value),flush=True)
    # Docker health means the observer loop is alive, independent of the alerts.
    return 0 if fresh else 1
