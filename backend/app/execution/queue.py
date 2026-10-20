"""Durable local queue. DB time and current attempt fence every protected write.

Claims/recovery take project -> run -> attempt locks. Inference never holds them.
No remote submission implementation lives here: ambiguous dispatch is terminal.
"""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select,insert,update,func
from ..persistence.transaction import unit_of_work
from ..security.errors import AuthError
from ..services.business import table,one
from ..services.reviews import Reviews,system_audit

WORKER_WORKFLOW='cc-postgres-worker-v1'
LEASE_SECONDS=90
HEARTBEAT_SECONDS=15
BACKOFF=(5,30)
WORKER_RETRY=dict(executor_kind='postgres_worker',max_attempts=3,lease_seconds=LEASE_SECONDS,
                  heartbeat_seconds=HEARTBEAT_SECONDS,backoff_seconds=list(BACKOFF),safe_retry='not_dispatched_only_v1')


class WorkerQueue(Reviews):
    def __init__(self,engine,storage,adapter,*,lease_seconds=LEASE_SECONDS):
        super().__init__(engine,None,storage,adapter,executor_kind='postgres_worker')
        self.lease_seconds=lease_seconds
        self.config=adapter.describe()

    def candidates(self,c,*,expired=False):
        runs=table('review_runs');attempts=table('review_attempts')
        query=select(runs.c.run_id,runs.c.project_id).where(runs.c.origin_kind=='service_execution',runs.c.executor_kind=='postgres_worker')
        if expired:
            query=query.join(attempts,runs.c.active_attempt_id==attempts.c.attempt_id).where(
                runs.c.execution_status=='running',attempts.c.execution_status=='active',attempts.c.lease_expires_at<=func.clock_timestamp())
            query=query.order_by(attempts.c.lease_expires_at,runs.c.run_id)
        else:
            query=query.where(runs.c.execution_status=='queued',runs.c.available_at<=func.clock_timestamp(),
                runs.c.workflow_version==WORKER_WORKFLOW,runs.c.requested_config==self.config,runs.c.retry_policy_jsonb==WORKER_RETRY)
            query=query.order_by(runs.c.available_at,runs.c.run_id)
        return c.execute(query.limit(64)).mappings().all()

    def try_run(self,c,candidate):
        # Never acquire a run lock before its project. Busy projects are skipped too.
        projects=table('projects');runs=table('review_runs')
        if c.execute(select(projects.c.project_id).where(projects.c.project_id==candidate['project_id'])
                     .with_for_update(key_share=True,skip_locked=True)).scalar_one_or_none() is None:return None
        row=c.execute(select(runs).where(runs.c.run_id==candidate['run_id']).with_for_update(skip_locked=True)).mappings().one_or_none()
        return dict(row) if row else None

    def claim_next(self):
        # If COMMIT is unconfirmed, caller must not execute; the lease recovery path owns it.
        with unit_of_work(self.engine) as c:
            for candidate in self.candidates(c):
                run=self.try_run(c,candidate)
                if run is None or run['execution_status']!='queued':continue
                now=c.exec_driver_sql('SELECT clock_timestamp()').scalar_one()
                if run['available_at']>now:continue
                attempts=table('review_attempts')
                number=c.execute(select(func.coalesce(func.max(attempts.c.attempt_no),0)).where(attempts.c.run_id==run['run_id'])).scalar_one()+1
                if number>WORKER_RETRY['max_attempts']:raise AuthError('ATTEMPT_LIMIT_EXCEEDED',409)
                attempt,token=uuid4(),uuid4()
                c.execute(insert(attempts).values(attempt_id=attempt,run_id=run['run_id'],project_id=run['project_id'],asset_version_id=run['asset_version_id'],
                    attempt_no=number,stage='preprocessing',execution_status='active',dispatch_state='not_dispatched',worker_token=token,
                    heartbeat_at=now,lease_expires_at=now+timedelta(seconds=self.lease_seconds),requested_config=run['requested_config'],started_at=now))
                c.execute(update(table('review_runs')).where(table('review_runs').c.run_id==run['run_id']).values(execution_status='running',
                    active_attempt_id=attempt,current_stage='preprocessing',progress=0,available_at=None,started_at=run['started_at'] or now,updated_at=now))
                system_audit(c,run,'review.worker_claimed')
                claimed=one(c,'review_runs','run_id',run['run_id']),attempt,token
                break
            else:claimed=None
        return claimed

    def heartbeat(self,run_id,attempt_id,token):
        with unit_of_work(self.engine) as c:
            self.live(c,run_id,attempt_id,token)
            now=c.exec_driver_sql('SELECT clock_timestamp()').scalar_one()
            c.execute(update(table('review_attempts')).where(table('review_attempts').c.attempt_id==attempt_id)
                      .values(heartbeat_at=now,lease_expires_at=now+timedelta(seconds=self.lease_seconds)))

    def dispatch(self,run_id,attempt_id,token):
        with unit_of_work(self.engine) as c:
            _,attempt=self.live(c,run_id,attempt_id,token)
            if attempt['dispatch_state']!='not_dispatched':raise AuthError('ATTEMPT_ALREADY_DISPATCHED',409)
            c.execute(update(table('review_attempts')).where(table('review_attempts').c.attempt_id==attempt_id).values(dispatch_state='dispatching'))
        # A committed dispatch marker is required before the first OCR call.

    def close_attempt(self,c,run,attempt,*,code,recovering=False):
        safe=attempt['dispatch_state']=='not_dispatched' and not attempt['remote_job_id']
        retry=(recovering and safe and run['retry_policy_jsonb']==WORKER_RETRY and attempt['attempt_no']<WORKER_RETRY['max_attempts'])
        unknown=not safe
        error=dict(code=code,stage=run['current_stage'],outcome_unknown=unknown,
                   message='검수가 완료되지 않았습니다. 실행 기록을 확인한 뒤 명시적으로 재검수해 주세요.')
        values=dict(execution_status='unknown' if unknown else 'failed',retryable=retry,error_jsonb=error,finished_at=func.clock_timestamp())
        if unknown and attempt['dispatch_state'] in ('dispatching','submitted'):values['dispatch_state']='unknown'
        c.execute(update(table('review_attempts')).where(table('review_attempts').c.attempt_id==attempt['attempt_id']).values(**values))
        now=c.exec_driver_sql('SELECT clock_timestamp()').scalar_one()
        values=dict(active_attempt_id=None,updated_at=now)
        if retry:values.update(execution_status='queued',current_stage='queued',progress=0,error_jsonb=None,
                               available_at=now+timedelta(seconds=BACKOFF[attempt['attempt_no']-1]))
        else:values.update(execution_status='failed',current_stage='failed',available_at=None,error_jsonb=error,finished_at=now)
        c.execute(update(table('review_runs')).where(table('review_runs').c.run_id==run['run_id']).values(**values))
        system_audit(c,run,'review.worker_requeued' if retry else 'review.worker_failed')
        return 'requeued' if retry else 'failed'

    def fail(self,run_id,attempt_id,token,*,code='REVIEW_EXECUTION_FAILED',**_):
        with unit_of_work(self.engine) as c:
            try:run,attempt=self.live(c,run_id,attempt_id,token)
            except AuthError as error:
                if error.code=='ATTEMPT_AUTHORITY_LOST':return False
                raise
            self.close_attempt(c,run,attempt,code=code)
        return True

    def recover_expired(self):
        result={}
        with unit_of_work(self.engine) as c:
            for candidate in self.candidates(c,expired=True):
                run=self.try_run(c,candidate)
                if run is None or run['execution_status']!='running':continue
                attempt=one(c,'review_attempts','attempt_id',run['active_attempt_id'],lock=True)
                now=c.exec_driver_sql('SELECT clock_timestamp()').scalar_one()
                if attempt['execution_status']!='active' or attempt['lease_expires_at'] is None or attempt['lease_expires_at']>now:continue
                code='LEASE_EXPIRED_BEFORE_DISPATCH' if attempt['dispatch_state']=='not_dispatched' else 'EXECUTION_OUTCOME_UNKNOWN'
                result[str(run['run_id'])]=self.close_attempt(c,run,attempt,code=code,recovering=True)
        return result
