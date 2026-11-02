"""M008-backed authenticated command service and durable job/step repository.

Uses the existing app Engine, unit_of_work and Authorization. No connection,
migration, credentials, model client or worker is opened by construction/import.
"""
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Column, Integer, MetaData, String, Table, select, insert, update, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID as PGUUID

from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from backend.app.services.business import table as business_table
from .contracts import Approval, AssetRef, Context, DocumentRef, RunSnapshot, StepTimeline
from .composition import TeamConfiguration
from .ports import Agents
from .runtime import Workflow, _boundary

metadata = MetaData(schema='agent_framework')
def _table(name, columns):
    return Table(name, metadata, *(Column(key, kind) for key, kind in columns))

runs = _table('runs', [('run_id',String),('project_id',PGUUID(as_uuid=True)),('actor_id',PGUUID(as_uuid=True)),
    ('parent_run_id',String),('input_json',JSONB),('input_sha256',String),('configuration_json',JSONB),
    ('configuration_sha256',String),('runtime_sha256',String),('snapshot_json',JSONB),
    ('created_at',TIMESTAMP(timezone=True)),('updated_at',TIMESTAMP(timezone=True))])
jobs = _table('jobs', [('job_id',PGUUID(as_uuid=True)),('run_id',String),('project_id',PGUUID(as_uuid=True)),
    ('phase',String),('state',String),('attempt_no',Integer),('worker_token',PGUUID(as_uuid=True)),
    ('lease_expires_at',TIMESTAMP(timezone=True)),('heartbeat_at',TIMESTAMP(timezone=True)),
    ('created_at',TIMESTAMP(timezone=True)),('finished_at',TIMESTAMP(timezone=True)),('error_json',JSONB)])
steps = _table('steps', [('run_id',String),('ordinal',Integer),('role',String),('state',String),
    ('record_json',JSONB),('input_sha256',String),('output_json',JSONB),('output_sha256',String)])
approvals = _table('approvals', [('approval_id',PGUUID(as_uuid=True)),('run_id',String),('project_id',PGUUID(as_uuid=True)),
    ('actor_id',PGUUID(as_uuid=True)),('decision',String),('payload_json',JSONB),('payload_sha256',String),
    ('approved_at',TIMESTAMP(timezone=True))])
events = _table('events', [('event_id',PGUUID(as_uuid=True)),('run_id',String),('kind',String),
    ('metadata_json',JSONB),('recorded_at',TIMESTAMP(timezone=True))])


def payload(value):
    return value.model_dump(mode='json')


def digest(value):
    if hasattr(value,'model_dump'): value = payload(value)
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),
                             allow_nan=False).encode()).hexdigest()


def runtime_revision():
    """Pin common execution code; team factories must document their own model revisions."""
    root = Path(__file__).parent
    return sha256(b''.join(p.relative_to(root).as_posix().encode()+b'\0'+p.read_bytes()
                          for p in sorted(root.rglob('*.py')))).hexdigest()


def coordinator():
    # The state-transition path is shared with the memory demo. Deferred commands
    # must never call a model inside a database transaction.
    class NoCall:
        def rejected(self,*_): raise RuntimeError('Adapters execute only in a framework worker')
        extract = inspect = validate = report = rejected
    adapter = NoCall()
    return Workflow(Agents(adapter,adapter,adapter,adapter))


class PostgresStore:
    def __init__(self, engine, configuration, authorization=None):
        self.engine = engine
        self.configuration = TeamConfiguration.model_validate(configuration.model_dump(mode='python',warnings=False))
        self.config_hash = digest(self.configuration)
        self.runtime_hash = runtime_revision()
        self.authorization = authorization

    def event(self,c,run_id,kind,**metadata):
        c.execute(insert(events).values(event_id=uuid4(),run_id=run_id,kind=kind,metadata_json=metadata))

    def _access(self,c,subject,context,*,write=True):
        if self.authorization is None: raise AuthError('AUTHENTICATION_REQUIRED',401)
        if write and str(getattr(subject,'user_id','')) != context.actor_id:
            raise AuthError('FRAMEWORK_ACTOR_MISMATCH',403)
        try: pid=UUID(context.project_id); UUID(context.actor_id)
        except ValueError: raise AuthError('FRAMEWORK_UUID_REQUIRED',422) from None
        self.authorization.project(c,subject,pid,action='write' if write else 'read')

    @staticmethod
    def _row(c,run_id,*,lock=False):
        query=select(runs).where(runs.c.run_id==run_id)
        row=c.execute(query.with_for_update() if lock else query).mappings().one_or_none()
        if row is None:raise AuthError('FRAMEWORK_RUN_NOT_FOUND',404)
        return dict(row)

    def _save(self,c,snapshot):
        # Revalidate even internally copied snapshots before the durable boundary.
        snapshot=_boundary(RunSnapshot,snapshot)
        c.execute(update(runs).where(runs.c.run_id==snapshot.context.run_id).values(
            snapshot_json=payload(snapshot),updated_at=func.clock_timestamp()))

    def _job(self,c,snapshot,phase):
        c.execute(insert(jobs).values(job_id=uuid4(),run_id=snapshot.context.run_id,
            project_id=UUID(snapshot.context.project_id),phase=phase,state='queued',attempt_no=0))
        self.event(c,snapshot.context.run_id,'job.queued',phase=phase)

    def _create(self,c,snapshot,document,asset,*,phase=None):
        fixed={'context':payload(snapshot.context),'document':payload(document),'asset':payload(asset)}
        c.execute(insert(runs).values(run_id=snapshot.context.run_id,project_id=UUID(snapshot.context.project_id),
            actor_id=UUID(snapshot.context.actor_id),parent_run_id=snapshot.context.parent_run_id,
            input_json=fixed,input_sha256=digest(fixed),configuration_json=payload(self.configuration),
            configuration_sha256=self.config_hash,runtime_sha256=self.runtime_hash,snapshot_json=payload(snapshot)))
        for record in snapshot.step_records:
            c.execute(insert(steps).values(run_id=snapshot.context.run_id,ordinal=record.ordinal,role=record.role,
                state=record.status,record_json=payload(record),input_sha256=digest(snapshot.criteria)))
        self.event(c,snapshot.context.run_id,'run.created',parent_run_id=snapshot.context.parent_run_id)
        if phase:self._job(c,snapshot,phase)

    def _restore(self,row):
        flow=coordinator();flow.profile=row['snapshot_json']['profile']
        flow.restore(RunSnapshot.model_validate(row['snapshot_json']),
            DocumentRef.model_validate(row['input_json']['document']),AssetRef.model_validate(row['input_json']['asset']))
        return flow

    def start(self,subject,context,document,asset):
        context,document,asset=_boundary(Context,context),_boundary(DocumentRef,document),_boundary(AssetRef,asset)
        fixed={'context':payload(context),'document':payload(document),'asset':payload(asset)}
        with unit_of_work(self.engine) as c:
            self._access(c,subject,context)
            # Project locking serializes same-project admission and UUID collisions.
            old=c.execute(select(runs).where(runs.c.run_id==context.run_id)).mappings().one_or_none()
            if old:
                if (old['project_id']!=UUID(context.project_id) or old['input_sha256']!=digest(fixed)
                        or old['configuration_sha256']!=self.config_hash or old['runtime_sha256']!=self.runtime_hash):
                    raise AuthError('FRAMEWORK_IDEMPOTENCY_CONFLICT',409)
                return RunSnapshot.model_validate(old['snapshot_json'])
            flow=coordinator();flow.profile=self.configuration.profile
            snapshot=flow.start(context,document,asset,defer=True)
            self._create(c,snapshot,document,asset,phase='extract')
        return snapshot

    def get(self,subject,run_id):
        with unit_of_work(self.engine) as c:
            row=self._row(c,run_id)
            snapshot=RunSnapshot.model_validate(row['snapshot_json'])
            self._access(c,subject,snapshot.context,write=False)
        return snapshot

    def get_steps(self,subject,run_id):
        snapshot=self.get(subject,run_id)
        return StepTimeline(context=snapshot.context,profile=snapshot.profile,storage='postgres',
            execution_status=snapshot.execution_status,records=snapshot.step_records)

    def approve(self,subject,run_id,approval):
        approval=_boundary(Approval,approval)
        with unit_of_work(self.engine) as c:
            self._access(c,subject,approval.context)
            row=self._row(c,run_id,lock=True)
            if row['project_id']!=UUID(approval.context.project_id):raise AuthError('FRAMEWORK_RUN_NOT_FOUND',404)
            existing=c.execute(select(approvals).where(approvals.c.run_id==run_id,
                approvals.c.decision==approval.decision)).mappings().one_or_none()
            if existing:
                if existing['payload_sha256']!=digest(approval):raise AuthError('FRAMEWORK_APPROVAL_CONFLICT',409)
                return RunSnapshot.model_validate(row['snapshot_json'])
            if (approval.decision=='approve_criteria' and (row['configuration_sha256']!=self.config_hash
                    or row['runtime_sha256']!=self.runtime_hash)):
                raise AuthError('FRAMEWORK_CONFIGURATION_CHANGED',409)
            flow=self._restore(row)
            snapshot=flow.approve(run_id,approval,defer=True)
            c.execute(insert(approvals).values(approval_id=uuid4(),run_id=run_id,project_id=row['project_id'],
                actor_id=UUID(approval.actor_id),decision=approval.decision,payload_json=payload(approval),payload_sha256=digest(approval)))
            self._save(c,snapshot)
            self.event(c,run_id,'approval.recorded',decision=approval.decision,actor_id=approval.actor_id)
            if approval.decision=='approve_criteria':self._job(c,snapshot,'review')
        return snapshot

    def rereview(self,subject,parent_id,context,asset):
        context,asset=_boundary(Context,context),_boundary(AssetRef,asset)
        with unit_of_work(self.engine) as c:
            self._access(c,subject,context)
            parent=self._row(c,parent_id,lock=True)
            if parent['project_id']!=UUID(context.project_id):raise AuthError('FRAMEWORK_RUN_NOT_FOUND',404)
            document=DocumentRef.model_validate(parent['input_json']['document'])
            fixed={'context':payload(context),'document':payload(document),'asset':payload(asset)}
            existing=c.execute(select(runs).where(runs.c.parent_run_id==parent_id)).mappings().one_or_none()
            if existing:
                if existing['input_sha256']!=digest(fixed):raise AuthError('FRAMEWORK_REVISION_CONFLICT',409)
                return RunSnapshot.model_validate(existing['snapshot_json'])
            flow=self._restore(parent)
            snapshot=flow.rereview(parent_id,context,asset)
            self._create(c,snapshot,document,asset)
        return snapshot

    def approval_history(self,subject,run_id):
        snapshot=self.get(subject,run_id)
        with unit_of_work(self.engine) as c:
            self._access(c,subject,snapshot.context,write=False)
            rows=c.execute(select(approvals).where(approvals.c.run_id==run_id).order_by(approvals.c.approved_at)).mappings().all()
            return [{'approval_id':str(r['approval_id']),'approved_at':r['approved_at'].isoformat(),
                     'approval':r['payload_json']} for r in rows]

    def worker_access(self,c,project_id,actor_id):
        # A queued command may outlive its login session. Worker rechecks the
        # existing account, membership and project rather than extending a session.
        project=c.execute(select(business_table('projects')).where(business_table('projects').c.project_id==project_id)
                          .with_for_update(key_share=True)).mappings().one_or_none()
        user=c.execute(select(business_table('users')).where(business_table('users').c.user_id==actor_id)
                       .with_for_update(read=True)).mappings().one_or_none()
        members=business_table('project_members')
        member=c.execute(select(members).where(members.c.project_id==project_id,members.c.user_id==actor_id)
                         .with_for_update(read=True)).mappings().one_or_none()
        if (not project or project['business_state']!='active' or not user or not user['is_active']
                or not member or not member['is_active'] or member['role'] not in ('owner','editor')):
            raise AuthError('FRAMEWORK_ACCESS_REVOKED',403)
