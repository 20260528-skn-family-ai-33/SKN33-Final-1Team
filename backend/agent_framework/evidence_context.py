"""PG -> fixed Neo4j query -> PG revalidation -> immutable 3.0.0 evidence.

Only approved original/value sources enter model input in this policy. Relationship
history is an audit query, never a substitute for observations or a new approval.
"""
import json
from time import perf_counter
from uuid import UUID

from sqlalchemy import text
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from .contracts import EvidenceBundle,RunSnapshot
from .context import freeze_approved_context
from .postgres import digest,payload
from .graph_projection import source_rows
from .graph_store import FrameworkGraph,GraphUnavailable

POLICY='af-approved-sources-v1'


def enabled(configuration):
    return configuration.context_preparation is not None


def stored(c,run_id):
    return c.execute(text('SELECT input_sha256,context_json,context_sha256,trace_json '
        'FROM agent_framework.evidence_contexts WHERE run_id=:r'),{'r':run_id}).mappings().one_or_none()


def check_stored(c,row,snapshot,base):
    record=stored(c,row['run_id'])
    if (record is None or record['input_sha256']!=row['input_sha256']
            or digest(record['context_json'])!=record['context_sha256']):
        raise AuthError('EVIDENCE_CONTEXT_INTEGRITY_INVALID',503)
    bundle=EvidenceBundle.model_validate(record['context_json'])
    if (snapshot.evidence!=bundle or bundle.context!=base.context or bundle.evidence!=base.evidence
            or bundle.synthetic!=base.synthetic or record['trace_json'].get('policy')!=POLICY):
        raise AuthError('EVIDENCE_CONTEXT_INTEGRITY_INVALID',503)
    from .validation import validate_evidence
    validate_evidence(snapshot.context,snapshot.criteria.criteria,bundle)
    return record


def _authoritative(c,queue,claim):
    from .criteria_review import PostgresCriteriaReview
    row,_job,_now=queue._live(c,claim)
    snapshot=RunSnapshot.model_validate(row['snapshot_json'])
    baseline=row['input_json'].get('baseline')
    if baseline is None:raise AuthError('APPROVED_BASELINE_REQUIRED',409)
    approved=PostgresCriteriaReview(queue.store)._approved(c,baseline['source_run_id'])
    criteria=approved.criteria.version.criteria
    base=freeze_approved_context(snapshot.context,criteria,synthetic=snapshot.synthetic)
    state=c.execute(text('SELECT revision FROM agent_framework.graph_state WHERE project_id=:p'),
        {'p':claim.project_id}).scalar_one_or_none()
    if state is None:raise AuthError('FRAMEWORK_GRAPH_SCHEMA_NOT_READY',503)
    return row,snapshot,base,state,source_rows(baseline['criteria_version_id'],[payload(x) for x in criteria])


def prepare_for_claim(queue,claim):
    if not enabled(queue.store.configuration):return
    started=perf_counter()
    with unit_of_work(queue.engine) as c:
        row,snapshot,base,revision,expected=_authoritative(c,queue,claim)
        if snapshot.evidence and snapshot.evidence.context_id.startswith('rag-'):
            check_stored(c,row,snapshot,base)
            return
        if any(s.status=='running' for s in snapshot.step_records):raise AuthError('FRAMEWORK_CALL_ALREADY_STARTED',409)
        version_id=snapshot.context.criteria_version_id
    own=queue.graph is None;graph=queue.graph or FrameworkGraph()
    observed=None;backend='neo4j';fallback=None;graph_relations=None
    try:
        result=graph.approved_sources(str(claim.project_id),version_id)
        observed=result['revision']
        if observed!=revision:raise GraphUnavailable('GRAPH_REVISION_MISMATCH')
        if result['sources']!=expected:raise GraphUnavailable('GRAPH_SOURCE_MISMATCH')
        graph_relations=result.get('relations')
    except GraphUnavailable as error:
        backend='postgresql';fallback=error.code
    finally:
        if own:graph.close()
    # No PG transaction was open while the derived store was queried.
    with unit_of_work(queue.engine) as c:
        row,snapshot,confirmed,current_revision,current_sources=_authoritative(c,queue,claim)
        if snapshot.evidence and snapshot.evidence.context_id.startswith('rag-'):
            check_stored(c,row,snapshot,confirmed);return
        if confirmed!=base or current_sources!=expected:
            raise AuthError('EVIDENCE_SOURCE_CHANGED',409)
        if current_revision!=revision:
            backend='postgresql';fallback='POSTGRES_REVISION_CHANGED'
        revision=current_revision
        from .graph_projection import criteria_impact
        history=criteria_impact(c,claim.project_id,version_id,confirmed.evidence[0].criterion_ids[0])
        relations={'run_ids':sorted(r['run_id'] for r in history['runs']),
            'approval_ids':sorted(r['approval_id'] for r in history['approvals']),
            'truncated':history['truncated']}
        if backend=='neo4j' and graph_relations!=relations:
            backend='postgresql';fallback='GRAPH_RELATION_MISMATCH'
        # SQL fallback is restricted to precisely the same approved-source relation.
        trace={'policy':POLICY,'backend':backend,'fallback_reason':fallback,
            'required_revision':revision,'observed_graph_revision':observed,
            'source_keys':current_sources,'baseline_sha256':digest(row['input_json']['baseline']),
            'relations':relations,
            'elapsed_ms':round((perf_counter()-started)*1000,3)}
        identity={'input':row['input_sha256'],'revision':revision,'sources':current_sources,'policy':POLICY}
        bundle=confirmed.model_copy(update={'context_id':'rag-'+digest(identity),
            'postgres_revision':revision,'graph_revision':observed if backend=='neo4j' else None,
            'warnings':('Approved original/value sources only; historical decisions are not model evidence.',)+
                ((f'PostgreSQL equivalent-query fallback: {fallback}',) if fallback else ())})
        bundle=EvidenceBundle.model_validate(payload(bundle))
        c.execute(text('INSERT INTO agent_framework.evidence_contexts '
            '(run_id,input_sha256,context_json,context_sha256,trace_json) '
            'VALUES(:r,:i,CAST(:c AS jsonb),:h,CAST(:t AS jsonb))'),
            {'r':claim.run_id,'i':row['input_sha256'],'c':json.dumps(payload(bundle)),
             'h':digest(bundle),'t':json.dumps(trace)})
        queue.store._save(c,snapshot.model_copy(update={'evidence':bundle}))
        queue.store.event(c,claim.run_id,'context.prepared',backend=backend,context_sha256=digest(bundle))


def read_context(store,subject,run_id):
    with unit_of_work(store.engine) as c:
        row=store._row(c,run_id);snapshot=RunSnapshot.model_validate(row['snapshot_json'])
        store._access(c,subject,snapshot.context,write=False)
        from .graph_projection import installed
        if not installed(c):raise AuthError('FRAMEWORK_GRAPH_SCHEMA_NOT_READY',503)
        record=stored(c,run_id)
        if record is None:raise AuthError('EVIDENCE_CONTEXT_NOT_READY',409)
        from .poster_review import validate_pinned_review
        validate_pinned_review(store,c,row)
        return {'evidence':record['context_json'],'sha256':record['context_sha256'],'retrieval':record['trace_json']}
