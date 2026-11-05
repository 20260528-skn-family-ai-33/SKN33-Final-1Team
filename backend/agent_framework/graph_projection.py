"""Transactional AF outbox and fenced delivery without a PG transaction over Neo4j I/O."""
from uuid import UUID, uuid4

from sqlalchemy import text
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError

GRAPH_EVENTS = frozenset({'run.created','step.completed','criteria.version_created',
    'criteria.approved','poster.review_created','approval.recorded','run.failed'})


def installed(c):
    return c.execute(text("SELECT to_regclass('agent_framework.graph_state')")).scalar_one() is not None


def mark_project_changed(c, project_id, kind, target_id):
    if not installed(c):
        return
    pid=UUID(str(project_id))
    revision=c.execute(text('INSERT INTO agent_framework.graph_state(project_id,revision) VALUES(:p,1) '
        'ON CONFLICT(project_id) DO UPDATE SET revision=agent_framework.graph_state.revision+1, '
        'available_at=CASE WHEN :reset THEN clock_timestamp() ELSE agent_framework.graph_state.available_at END '
        'RETURNING revision'),{'p':pid,'reset':kind in ('graph.rebuild_requested','graph.recovery_requested')}).scalar_one()
    c.execute(text('INSERT INTO agent_framework.graph_outbox(event_id,project_id,revision,kind,target_id) '
        'VALUES(:e,:p,:r,:k,:t)'),{'e':uuid4(),'p':pid,'r':revision,'k':kind,'t':str(target_id)})


def source_rows(version_id, criteria):
    rows=[]
    for item in criteria:
        cid=item['criterion_id']
        rows.append({'criterion_id':cid,'source_key':f'source:{version_id}:{cid}:document',
                     'source_hash':item['source']['source_hash']})
        value=item.get('value_source')
        if value:
            rows.append({'criterion_id':cid,'source_key':f'source:{version_id}:{cid}:value',
                         'source_hash':value['quote_sha256']})
    return sorted(rows,key=lambda row:(row['criterion_id'],row['source_key']))


def project_snapshot(c, project_id):
    """Caller supplies one repeatable-read PG snapshot, then closes it before graph I/O."""
    pid=UUID(str(project_id));nodes={};edges=set()
    def node(key,kind,**props):nodes[key]={'key':key,'kind':kind,**props}
    def edge(a,b,kind):edges.add((a,b,kind))
    project='project:'+str(pid);node(project,'project')
    docs=c.execute(text('SELECT document_version_id,document_id,sha256,previous_version_id FROM '
        'agent_framework.document_versions WHERE project_id=:p'),{'p':pid}).mappings().all()
    for row in docs:
        key='document:'+str(row['document_version_id']);node(key,'document_version',sha256=row['sha256'])
        edge(project,key,'contains')
        if row['previous_version_id']:edge('document:'+str(row['previous_version_id']),key,'revised_as')
    versions=c.execute(text('SELECT criteria_version_id,document_version_id,snapshot_json,snapshot_sha256 '
        'FROM agent_framework.criteria_versions WHERE project_id=:p'),{'p':pid}).mappings().all()
    bindings=c.execute(text('SELECT criteria_version_id,approval_id,source_run_id FROM '
        'agent_framework.criteria_approval_bindings WHERE project_id=:p'),{'p':pid}).mappings().all()
    approved={str(row['criteria_version_id']) for row in bindings}
    for row in versions:
        vid=str(row['criteria_version_id']);key='criteria:'+vid
        raw=row['snapshot_json'];items=raw.get('criteria',[])
        node(key,'criteria_version',approved=vid in approved,sha256=row['snapshot_sha256'])
        edge(key,'document:'+str(row['document_version_id']),'based_on')
        for item in items:
            ck=f'criterion:{vid}:{item["criterion_id"]}';node(ck,'criterion',criterion_id=item['criterion_id'])
            edge(key,ck,'contains')
        for source in source_rows(vid,items):
            node(source['source_key'],'source',source_hash=source['source_hash'])
            edge(f'criterion:{vid}:{source["criterion_id"]}',source['source_key'],'sourced_from')
    runs=c.execute(text('SELECT run_id,parent_run_id,input_json,snapshot_json FROM '
        'agent_framework.runs WHERE project_id=:p'),{'p':pid}).mappings().all()
    for row in runs:
        key='run:'+row['run_id'];snap=row['snapshot_json'];fixed=row['input_json']
        node(key,'run',state=snap.get('execution_status','unknown'),synthetic=bool(snap.get('synthetic',False)))
        edge(project,key,'contains')
        doc=fixed.get('document') or {};edge(key,'document:'+str(doc.get('document_version_id')),'reads')
        baseline=fixed.get('baseline') or {};vid=baseline.get('criteria_version_id') or snap.get('approved_criteria_version_id')
        if vid:edge(key,'criteria:'+vid,'applies')
        asset=fixed.get('asset') or {}
        if asset:
            ak='poster:'+asset['asset_version_id'];node(ak,'poster_version',sha256=asset['sha256']);edge(key,ak,'inspects')
        if row['parent_run_id']:edge('run:'+row['parent_run_id'],key,'reinspected_as')
    approvals=c.execute(text('SELECT approval_id,run_id,decision FROM agent_framework.approvals '
        'WHERE project_id=:p'),{'p':pid}).mappings().all()
    for row in approvals:
        key='approval:'+str(row['approval_id']);node(key,'approval',decision=row['decision'])
        edge(key,'run:'+row['run_id'],'approves')
    for row in bindings:edge('approval:'+str(row['approval_id']),'criteria:'+str(row['criteria_version_id']),'approves')
    return sorted(nodes.values(),key=lambda n:n['key']),[{'source':a,'target':b,'kind':k}
        for a,b,k in sorted(edges) if a in nodes and b in nodes]


class ProjectionWorker:
    def __init__(self,engine,graph,*,lease_seconds=120):
        self.engine,self.graph,self.lease_seconds=engine,graph,lease_seconds
        self.last_outcome='idle'

    def deliver_one(self,*,project_id=None):
        self.last_outcome='idle'
        token=uuid4()
        with unit_of_work(self.engine) as c:
            scope=' AND project_id=:p ' if project_id is not None else ' '
            row=c.execute(text('SELECT project_id FROM agent_framework.graph_state WHERE revision>applied_revision '
                'AND available_at<=clock_timestamp() AND (lease_expires_at IS NULL OR lease_expires_at<=clock_timestamp()) '
                +scope+'ORDER BY available_at,project_id FOR UPDATE SKIP LOCKED LIMIT 1'),
                {'p':UUID(str(project_id))} if project_id is not None else {}).mappings().one_or_none()
            if row is None:return False
            pid=row['project_id']
            c.execute(text('UPDATE agent_framework.graph_state SET lease_token=:t,lease_expires_at='
                "clock_timestamp()+make_interval(secs=>:s) WHERE project_id=:p"),{'t':token,'s':self.lease_seconds,'p':pid})
        try:
            with self.engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
                with c.begin():
                    c.execute(text('SET TRANSACTION READ ONLY'))
                    revision=c.execute(text('SELECT revision FROM agent_framework.graph_state WHERE project_id=:p'),{'p':pid}).scalar_one()
                    nodes,edges=project_snapshot(c,pid)
            result=self.graph.replace_project(str(pid),nodes,edges,revision)
            if result is False:
                from .graph_recovery import content_hash
                from .graph_store import GraphUnavailable
                observed=self.graph.read_projection(str(pid))
                if observed['revision']!=revision or observed['unmanaged'] or content_hash(observed['nodes'],observed['edges'])!=content_hash(nodes,edges):
                    raise GraphUnavailable('GRAPH_DELIVERY_NOT_CONFIRMED')
        except Exception:
            self.last_outcome='deferred'
            with unit_of_work(self.engine) as c:
                c.execute(text('UPDATE agent_framework.graph_state SET lease_token=NULL,lease_expires_at=NULL,'
                    'attempt_no=attempt_no+1,error_json=CAST(:e AS jsonb),available_at=clock_timestamp()+'
                    'make_interval(secs=>LEAST(300,power(2,LEAST(attempt_no+1,8))::integer)) '
                    'WHERE project_id=:p AND lease_token=:t AND lease_expires_at>clock_timestamp()'),
                    {'p':pid,'t':token,'e':'{"code":"GRAPH_SYNC_UNAVAILABLE"}'})
            return True
        with unit_of_work(self.engine) as c:
            live=c.execute(text('UPDATE agent_framework.graph_state SET applied_revision=GREATEST(applied_revision,:r),'
                'lease_token=NULL,lease_expires_at=NULL,attempt_no=0,error_json=NULL,synced_at=clock_timestamp(),'
                'available_at=clock_timestamp() WHERE project_id=:p AND lease_token=:t '
                'AND lease_expires_at>clock_timestamp() RETURNING project_id'),{'p':pid,'t':token,'r':revision}).scalar_one_or_none()
            if live is not None:
                c.execute(text('UPDATE agent_framework.graph_outbox SET processed_at=clock_timestamp() '
                    'WHERE project_id=:p AND revision<=:r AND processed_at IS NULL'),{'p':pid,'r':revision})
            self.last_outcome='synced' if live is not None else 'authority_lost'
        return True


def graph_status(c,project_id):
    row=c.execute(text('SELECT revision,applied_revision,attempt_no,error_json,synced_at '
        'FROM agent_framework.graph_state WHERE project_id=:p'),{'p':UUID(str(project_id))}).mappings().one_or_none()
    if row is None:return {'status':'not_projected','required_revision':0,'applied_revision':None}
    return {'status':'error' if row['error_json'] else 'synced' if row['applied_revision']==row['revision'] else 'pending',
        'required_revision':row['revision'],'applied_revision':row['applied_revision'],
        'attempt_no':row['attempt_no'],'error':row['error_json'],'synced_at':row['synced_at']}


def criteria_impact(c,project_id,version_id,criterion_id):
    """Authoritative bounded relationship query; criterion IDs are version-scoped strings, not UUIDs."""
    from .criteria_contracts import CriteriaVersion
    from .postgres import digest
    pid=UUID(str(project_id));vid=UUID(str(version_id))
    row=c.execute(text('SELECT snapshot_json,snapshot_sha256 FROM agent_framework.criteria_versions '
        'WHERE project_id=:p AND criteria_version_id=:v'),{'p':pid,'v':vid}).mappings().one_or_none()
    if row is None or digest(row['snapshot_json'])!=row['snapshot_sha256']:
        raise AuthError('RESOURCE_NOT_FOUND',404)
    version=CriteriaVersion.model_validate(row['snapshot_json'])
    if criterion_id not in {item.criterion_id for item in version.criteria}:raise AuthError('RESOURCE_NOT_FOUND',404)
    rows=c.execute(text("SELECT run_id,parent_run_id,snapshot_json->>'execution_status' AS status FROM agent_framework.runs "
        "WHERE project_id=:p AND (input_json->'baseline'->>'criteria_version_id'=:v OR "
        "snapshot_json->>'approved_criteria_version_id'=:v) ORDER BY created_at,run_id LIMIT 501"),{'p':pid,'v':str(vid)}).mappings().all()
    ids=[r['run_id'] for r in rows[:500]]
    approvals=c.execute(text('SELECT approval_id,run_id,decision FROM agent_framework.approvals '
        'WHERE project_id=:p ORDER BY approved_at,approval_id'),{'p':pid}).mappings().all()
    return {'criteria_version_id':str(vid),'criterion_id':criterion_id,'runs':[dict(r) for r in rows[:500]],
        'approvals':[{'approval_id':str(r['approval_id']),'run_id':r['run_id'],'decision':r['decision']}
                     for r in approvals if r['run_id'] in ids], 'truncated':len(rows)>500,'backend':'postgresql'}
