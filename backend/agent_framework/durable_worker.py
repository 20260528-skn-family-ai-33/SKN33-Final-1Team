"""Fenced PostgreSQL role worker. Inference never holds a DB transaction.

Expired claims without an in-flight role can resume committed results. A running
role marker means a call MAY have happened: never replay it automatically.
"""
from dataclasses import dataclass
from datetime import timedelta
from threading import Event, Thread
from time import perf_counter_ns
from uuid import uuid4
from typing import TypedDict

from sqlalchemy import select,insert,update,func
from langgraph.graph import StateGraph,START,END

from backend.app.persistence.transaction import unit_of_work
from backend.app.persistence.database import PersistenceError
from backend.app.security.errors import AuthError
from backend.app.services.business import table as business_table
from .contracts import (RunSnapshot,StepRecord,FrameworkError,ExtractRequest,
    InspectRequest,ValidateRequest,ReportRequest)
from .invocation import ROLE_CONTRACTS,InvocationFailure,invoke_role,accept_role_output
from .postgres import runs,jobs,steps,payload,digest
from .runtime import Workflow,_boundary

PHASE_ROLES={'extract':('A02',),'review':('A04_A05','A06','A07')}
RESULT_FIELDS={'A02':'criteria','A04_A05':'inspection','A06':'validated','A07':'report'}


def stage_request(snapshot,fixed,role):
    context=snapshot.context
    if role=='A02':return ExtractRequest(context=context,document=fixed['document'],synthetic=snapshot.synthetic)
    if role=='A07':return ReportRequest(context=context,validated=snapshot.validated,evidence=snapshot.evidence,synthetic=snapshot.synthetic)
    request=InspectRequest(context=context,criteria=snapshot.criteria.criteria,asset=fixed['asset'],
                           evidence=snapshot.evidence,synthetic=snapshot.synthetic)
    if role=='A04_A05':return request
    return ValidateRequest(**payload(request),inspection=snapshot.inspection)


@dataclass(frozen=True)
class Claim:
    job_id: object
    run_id: str
    project_id: object
    phase: str
    token: object


class PostgresQueue:
    def __init__(self,store,*,lease_seconds=90):
        if type(lease_seconds) is not int or not 1<=lease_seconds<=3600:raise ValueError('invalid lease')
        self.store,self.engine,self.lease_seconds=store,store.engine,lease_seconds

    def _lock(self,c,claim,*,skip=False):
        project=business_table('projects')
        if c.execute(select(project.c.project_id).where(project.c.project_id==claim.project_id)
                     .with_for_update(key_share=True,skip_locked=skip)).scalar_one_or_none() is None:return None
        run=c.execute(select(runs).where(runs.c.run_id==claim.run_id).with_for_update(skip_locked=skip)).mappings().one_or_none()
        job=c.execute(select(jobs).where(jobs.c.job_id==claim.job_id).with_for_update(skip_locked=skip)).mappings().one_or_none()
        if not run or not job:return None
        return dict(run),dict(job)

    def _live(self,c,claim,*,authorize=True):
        rows=self._lock(c,claim)
        if rows is None:raise AuthError('FRAMEWORK_AUTHORITY_LOST',409)
        run,job=rows
        now=c.execute(select(func.clock_timestamp())).scalar_one()
        if (job['state']!='running' or job['worker_token']!=claim.token or job['lease_expires_at']<=now
            or run['configuration_sha256']!=self.store.config_hash or run['runtime_sha256']!=self.store.runtime_hash):
            raise AuthError('FRAMEWORK_AUTHORITY_LOST',409)
        if authorize:self.store.worker_access(c,run['project_id'],run['actor_id'])
        return run,job,now

    def claim_next(self):
        claim=None
        with unit_of_work(self.engine) as c:
            candidates=c.execute(select(jobs).join(runs,runs.c.run_id==jobs.c.run_id).where(
                jobs.c.state=='queued',runs.c.configuration_sha256==self.store.config_hash,
                runs.c.runtime_sha256==self.store.runtime_hash).order_by(jobs.c.created_at,jobs.c.job_id).limit(64)).mappings().all()
            for candidate in candidates:
                candidate=Claim(candidate['job_id'],candidate['run_id'],candidate['project_id'],candidate['phase'],None)
                locked=self._lock(c,candidate,skip=True)
                if locked is None:continue
                run,job=locked
                if job['state']!='queued':continue
                now=c.execute(select(func.clock_timestamp())).scalar_one()
                if job['attempt_no']>=3:
                    self._close(c,run,job,FrameworkError(code='ATTEMPT_LIMIT_EXCEEDED',message='The safe claim limit was reached.'))
                    continue
                try:self.store.worker_access(c,run['project_id'],run['actor_id'])
                except AuthError:
                    self._close(c,run,job,FrameworkError(code='FRAMEWORK_ACCESS_REVOKED',message='Project execution access was revoked.'))
                    continue
                token=uuid4()
                c.execute(update(jobs).where(jobs.c.job_id==job['job_id']).values(state='running',attempt_no=job['attempt_no']+1,
                    worker_token=token,lease_expires_at=now+timedelta(seconds=self.lease_seconds),heartbeat_at=now))
                snapshot=RunSnapshot.model_validate(run['snapshot_json'])
                self.store._save(c,snapshot.model_copy(update={'execution_status':'extracting' if job['phase']=='extract' else 'reviewing'}))
                self.store.event(c,run['run_id'],'job.claimed',phase=job['phase'],attempt=job['attempt_no']+1)
                claim=Claim(job['job_id'],job['run_id'],job['project_id'],job['phase'],token)
                break
        # Only a confirmed commit returns authority to invoke any adapter.
        return claim

    def heartbeat(self,claim):
        with unit_of_work(self.engine) as c:
            _run,_job,now=self._live(c,claim)
            c.execute(update(jobs).where(jobs.c.job_id==claim.job_id).values(
                heartbeat_at=now,lease_expires_at=now+timedelta(seconds=self.lease_seconds)))

    def begin_step(self,claim,role):
        with unit_of_work(self.engine) as c:
            run,job,now=self._live(c,claim)
            snapshot=RunSnapshot.model_validate(run['snapshot_json'])
            completed={s.role for s in snapshot.step_records if s.status in ('completed','reused')}
            remaining=[r for r in PHASE_ROLES[job['phase']] if r not in completed]
            if not remaining or remaining[0]!=role:raise AuthError('FRAMEWORK_STAGE_CONFLICT',409)
            if any(s.status=='running' for s in snapshot.step_records):raise AuthError('FRAMEWORK_CALL_ALREADY_STARTED',409)
            request=stage_request(snapshot,run['input_json'],role)
            record=StepRecord(ordinal=len(snapshot.step_records)+1,role=role,status='running',
                              recorded_at=now,started_at=now,synthetic=snapshot.synthetic)
            c.execute(insert(steps).values(run_id=run['run_id'],ordinal=record.ordinal,role=role,state='running',
                                           record_json=payload(record),input_sha256=digest(request)))
            self.store._save(c,snapshot.model_copy(update={'step_records':snapshot.step_records+(record,)}))
            self.store.event(c,run['run_id'],'step.started',role=role,ordinal=record.ordinal)
        return request

    def complete_step(self,claim,role,request,result,duration_ms):
        with unit_of_work(self.engine) as c:
            run,job,now=self._live(c,claim)
            snapshot=RunSnapshot.model_validate(run['snapshot_json'])
            record=snapshot.step_records[-1]
            stored=c.execute(select(steps).where(steps.c.run_id==run['run_id'],steps.c.role==role)).mappings().one()
            if (record.role!=role or record.status!='running' or stored['state']!='running'
                    or stored['input_sha256']!=digest(request)):raise AuthError('FRAMEWORK_STAGE_CONFLICT',409)
            result=accept_role_output(role,request,result)
            synthetic=snapshot.synthetic or result.synthetic
            result=_boundary(type(result),result.model_copy(update={'synthetic':synthetic}))
            finished=record.model_copy(update={'status':'completed','finished_at':max(now,record.started_at),
                                              'duration_ms':duration_ms,'synthetic':synthetic})
            changes={RESULT_FIELDS[role]:result,'synthetic':synthetic,'stages':snapshot.stages+(role,),
                     'step_records':snapshot.step_records[:-1]+(finished,)}
            if role=='A06':changes['machine_verdict']=result.verdict
            if role in ('A02','A07'):changes['execution_status']='waiting_criteria' if role=='A02' else 'waiting_review'
            accepted=_boundary(RunSnapshot,snapshot.model_copy(update=changes))
            c.execute(update(steps).where(steps.c.run_id==run['run_id'],steps.c.role==role).values(state='completed',
                record_json=payload(finished),output_json=payload(result),output_sha256=digest(result)))
            self.store._save(c,accepted)
            self.store.event(c,run['run_id'],'step.completed',role=role,output_sha256=digest(result))
            if role in ('A02','A07'):self._finish_job(c,job,'completed')
        return accepted

    def _finish_job(self,c,job,state,*,error=None):
        c.execute(update(jobs).where(jobs.c.job_id==job['job_id']).values(state=state,worker_token=None,
            lease_expires_at=None,finished_at=func.clock_timestamp(),error_json=payload(error) if error else None))

    def _close(self,c,run,job,error,*,top_code=None,duration_ms=None):
        snapshot=RunSnapshot.model_validate(run['snapshot_json'])
        records=snapshot.step_records
        if records and records[-1].status=='running':
            now=c.execute(select(func.clock_timestamp())).scalar_one()
            record=records[-1]
            closed=record.model_copy(update={'status':'outcome_unknown' if error.outcome_unknown else 'failed',
                'finished_at':max(now,record.started_at),
                'duration_ms':None if error.outcome_unknown else (duration_ms if duration_ms is not None else max(0,(now-record.started_at).total_seconds()*1000)),'error':error})
            c.execute(update(steps).where(steps.c.run_id==run['run_id'],steps.c.ordinal==record.ordinal).values(
                state=closed.status,record_json=payload(closed)))
            records=records[:-1]+(closed,)
        status='outcome_unknown' if error.outcome_unknown else 'failed'
        top=error.model_copy(update={'code':top_code}) if top_code else error
        self.store._save(c,snapshot.model_copy(update={'execution_status':status,'error':top,'step_records':records}))
        self._finish_job(c,job,status,error=error)
        self.store.event(c,run['run_id'],'job.'+status,code=error.code)

    def fail(self,claim,exc,*,duration_ms=None):
        error=Workflow._step_error(exc)
        if isinstance(exc,AuthError) and exc.code=='FRAMEWORK_ACCESS_REVOKED':
            error=error.model_copy(update={'code':exc.code})
        with unit_of_work(self.engine) as c:
            run,job,_now=self._live(c,claim,authorize=False)
            self._close(c,run,job,error,top_code='contract_violation' if isinstance(exc,InvocationFailure) else None,duration_ms=duration_ms)

    def recover_expired(self):
        recovered={}
        with unit_of_work(self.engine) as c:
            candidates=c.execute(select(jobs).where(jobs.c.state=='running',jobs.c.lease_expires_at<=func.clock_timestamp())
                                 .order_by(jobs.c.lease_expires_at).limit(64)).mappings().all()
            for row in candidates:
                claim=Claim(row['job_id'],row['run_id'],row['project_id'],row['phase'],row['worker_token'])
                locked=self._lock(c,claim,skip=True)
                if locked is None:continue
                run,job=locked
                now=c.execute(select(func.clock_timestamp())).scalar_one()
                if job['state']!='running' or job['lease_expires_at']>now:continue
                snapshot=RunSnapshot.model_validate(run['snapshot_json'])
                if any(s.status=='running' for s in snapshot.step_records):
                    self._close(c,run,job,FrameworkError(code='EXECUTION_OUTCOME_UNKNOWN',
                        message='A worker stopped after a role dispatch marker. Reconcile before a new run.',outcome_unknown=True))
                    recovered[run['run_id']]='outcome_unknown'
                elif job['attempt_no']>=3:
                    self._close(c,run,job,FrameworkError(code='ATTEMPT_LIMIT_EXCEEDED',message='The safe claim limit was reached.'))
                    recovered[run['run_id']]='failed'
                else:
                    c.execute(update(jobs).where(jobs.c.job_id==job['job_id']).values(state='queued',worker_token=None,lease_expires_at=None))
                    self.store._save(c,snapshot.model_copy(update={'execution_status':'queued'}))
                    self.store.event(c,run['run_id'],'job.safely_requeued',attempt=job['attempt_no'])
                    recovered[run['run_id']]='queued'
        return recovered


class Lease:
    def __init__(self,queue,claim,interval):
        self.queue,self.claim,self.interval=queue,claim,interval
        self.stop=Event();self.lost=Event();self.thread=Thread(target=self.renew,daemon=True)
    def renew(self):
        while not self.stop.wait(self.interval):
            try:self.queue.heartbeat(self.claim)
            except Exception:self.lost.set();return
    def check(self):
        if self.lost.is_set():raise AuthError('FRAMEWORK_AUTHORITY_LOST',409)
    def __enter__(self):self.thread.start();return self
    def __exit__(self,*_):self.stop.set();self.thread.join(timeout=15);self.lost.set()


class _PhaseState(TypedDict):
    outcome: str


def execute_claim(queue,claim,agents,*,heartbeat_seconds=15,stop=None):
    """LangGraph schedules roles; committed PostgreSQL rows own resume decisions.

    A stop between committed roles safely releases the lease via expiry. No
    LangGraph checkpoint is claimed to share the business transaction.
    """
    with Lease(queue,claim,heartbeat_seconds) as authority:
        def execute_role(role):
            if stop is not None and stop.is_set():return 'draining'
            started=None
            try:
                authority.check()
                with unit_of_work(queue.engine) as c:
                    run,_job,_now=queue._live(c,claim)
                    snapshot=RunSnapshot.model_validate(run['snapshot_json'])
                if any(s.role==role and s.status=='completed' for s in snapshot.step_records):return 'continue'
                request=queue.begin_step(claim,role)
                started=perf_counter_ns()
                result=invoke_role(role,getattr(agents,ROLE_CONTRACTS[role][0]),request)
                duration=max(0,perf_counter_ns()-started)/1_000_000
                authority.check()
                queue.complete_step(claim,role,request,result,duration)
            except PersistenceError:
                # COMMIT may have succeeded. Do not guess, fail another step, or
                # call the provider again. The lease/recovery path reconciles it.
                return 'database_unavailable'
            except Exception as exc:
                if isinstance(exc,AuthError) and exc.code=='FRAMEWORK_AUTHORITY_LOST':
                    return 'authority_lost'
                try:
                    queue.fail(claim,exc,duration_ms=max(0,perf_counter_ns()-started)/1_000_000 if started is not None else None)
                except (AuthError,PersistenceError):return 'authority_lost'
                return 'outcome_unknown' if getattr(exc,'outcome_unknown',False) else 'failed'
            return 'continue'
        graph=StateGraph(_PhaseState)
        roles=PHASE_ROLES[claim.phase]
        for role in roles:
            def node(_state,role=role):return {'outcome':execute_role(role)}
            graph.add_node(role,node)
        graph.add_edge(START,roles[0])
        for index,role in enumerate(roles):
            following=roles[index+1] if index+1<len(roles) else END
            graph.add_conditional_edges(role,lambda state:'next' if state['outcome']=='continue' else 'stop',
                                        {'next':following,'stop':END})
        result=graph.compile().invoke({'outcome':'continue'},{'recursion_limit':12})
        return 'completed' if result['outcome']=='continue' else result['outcome']
