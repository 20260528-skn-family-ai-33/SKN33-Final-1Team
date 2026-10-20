"""C10 admission, current token authority and restart closure. No inference under DB locks."""
import hashlib
from uuid import UUID,uuid4
from sqlalchemy import select,insert,update,func
from ..hashing.canonical import digest
from ..persistence.transaction import unit_of_work
from ..persistence.database import PersistenceError
from ..security.errors import AuthError
from .business import table,one,audit,safe_transaction

WORKFLOW='cc-background-v1'
POLICY='all_selected_v1'
RETRY={'max_attempts':1,'executor_kind':'background_tasks'}


def advisory(value): return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8],'big',signed=True)


def input_payload(pid,image,display,version,config,*,workflow=WORKFLOW,retry=RETRY):
    return dict(project_id=str(pid),image=dict(image_id=str(image['asset_version_id']),original_sha256=image['original_sha256'],
        display_sha256=display['sha256'],width=image['display_width'],height=image['display_height'],
        coordinate_space='normalized_original_pixels',preprocessing_version=config['preprocessing']['version'],ocr_scale=2),
        criteria=dict(criteria_version_id=str(version['criteria_version_id']),criteria_hash=version['criteria_hash']),
        execution=dict(workflow_version=workflow,execution_target='local',requested_config=config,aggregation_policy_version=POLICY,retry_policy=retry))


def receipt(row):
    return dict(id=str(row['run_id']),status=row['execution_status'],project_id=str(row['project_id']),image_id=str(row['asset_version_id']),
        criteria_version_id=str(row['criteria_version_id']),retry_of=str(row['retry_of_run_id']) if row['retry_of_run_id'] else None)


def system_audit(connection,run,action):
    connection.execute(insert(table('audit_events')).values(event_id=uuid4(),project_id=run['project_id'],actor_kind='system',
        actor_user_id=None,action=action,target_type='review_run',target_id=run['run_id'],event_metadata={}))


class Reviews:
    def __init__(self,engine,business,storage,adapter,*,executor_kind='background_tasks'):
        self.engine,self.business,self.storage,self.adapter=engine,business,storage,adapter
        self.executor_kind=executor_kind
        if executor_kind=='postgres_worker':
            from ..execution.queue import WORKER_WORKFLOW,WORKER_RETRY
            self.workflow,self.retry=WORKER_WORKFLOW,WORKER_RETRY
        else:self.workflow,self.retry=WORKFLOW,RETRY

    def existing(self,c,subject,dev,operation,key,request_hash):
        runs=table('review_runs')
        row=c.execute(select(runs).where(runs.c.requested_by==subject.user_id,runs.c.request_operation==operation,runs.c.idempotency_key==key)).mappings().one_or_none()
        if row is None:return None
        if row['request_hash']!=request_hash:raise AuthError('IDEMPOTENCY_KEY_REUSED',409)
        self.business.project(c,subject,row['project_id'],dev,'write',allow_archived=True)
        return dict(row)

    @safe_transaction
    def admit(self,subject,dev,body,key,request_id,source_id=None):
        if key.version!=4:raise AuthError('INVALID_IDEMPOTENCY_KEY',422)
        operation='retry_review' if source_id else 'create_review'
        payload=dict(actor_user_id=str(subject.user_id),operation=operation,body=body)
        if source_id:payload['source_run_id']=str(source_id)
        request_hash=digest(operation,payload,'cc-request-jcs-v1')
        with unit_of_work(self.engine) as c:
            self.business.authorization.identity(c,subject,developer_allowed=dev)
            old=self.existing(c,subject,dev,operation,key,request_hash)
            if old:return receipt(old),True
            if source_id:
                source=self.business.authorization.resource(c,subject,'review_runs',source_id,action='write',developer_allowed=dev)
                pid,image_id,version_id=source['project_id'],source['asset_version_id'],source['criteria_version_id']
            else:pid,image_id,version_id=(UUID(body[name]) for name in ('project_id','image_id','criteria_version_id'))
            self.business.project(c,subject,pid,dev,'write')
            image=self.business.same_project(c,'asset_versions','asset_version_id',image_id,pid)
            version=self.business.same_project(c,'criteria_versions','criteria_version_id',version_id,pid)
            if version['target_asset_version_id']!=image_id:raise AuthError('RESOURCE_NOT_FOUND',404)
            files=[one(c,'stored_files','file_id',image[name]) for name in ('original_file_id','display_file_id')]
        # Replay has already returned; changed or unavailable runtime must not invalidate it.
        if getattr(self,'leader',None) is not None:self.leader.check()
        config=self.adapter.describe()
        from ..execution.output import raw_json
        raw_json(config)
        if config.get('execution_target')!='local' or config.get('preprocessing',{}).get('ocr_scale')!=2:
            raise AuthError('REVIEW_RUNTIME_UNAVAILABLE',503)
        for file in files:
            if file['state']!='stored':raise AuthError('FILE_UNAVAILABLE',503)
            try:
                with self.storage.verified(file['storage_key'],file['byte_size'],file['sha256']):pass
            except (OSError,ValueError,RuntimeError):raise AuthError('FILE_UNAVAILABLE',503) from None
        manifest=digest('review_input',input_payload(pid,image,files[1],version,config,workflow=self.workflow,retry=self.retry),'cc-manifest-jcs-v1')
        run_id=uuid4()
        try:
            with unit_of_work(self.engine) as c:
                # Global capacity and actor/operation/key locks precede project/run locks.
                capacity='cc-background-capacity-v1' if self.executor_kind=='background_tasks' else 'cc-postgres-worker-capacity-v1'
                c.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)',(advisory(capacity),))
                if getattr(self,'leader',None) is not None:self.leader.check()
                c.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)',(advisory(str(subject.user_id)+operation+str(key)),))
                old=self.existing(c,subject,dev,operation,key,request_hash)
                if old:return receipt(old),True
                project=self.business.project(c,subject,pid,dev,'write')
                if source_id:
                    source=one(c,'review_runs','run_id',source_id,lock=True)
                    if source['origin_kind']!='service_execution':raise AuthError('SOURCE_NOT_RETRYABLE',409)
                    if source['execution_status'] not in ('completed','failed'):raise AuthError('REVIEW_NOT_TERMINAL',409)
                version=self.business.same_project(c,'criteria_versions','criteria_version_id',version_id,pid)
                if version['confirmation_state']!='confirmed' or not version['sealed_at'] or version['origin_kind']!='service_confirmation':
                    raise AuthError('CRITERIA_NOT_CONFIRMED',409)
                draft=one(c,'criteria_drafts','draft_id',version['source_draft_id'],lock=True)
                if draft['revision']!=version['source_draft_revision'] or draft['state']!='editing':raise AuthError('CRITERIA_STALE',409)
                for file in files:
                    actual=one(c,'stored_files','file_id',file['file_id'])
                    if actual['state']!='stored' or actual['sha256']!=file['sha256']:raise AuthError('FILE_UNAVAILABLE',503)
                runs=table('review_runs')
                pending=c.execute(select(func.count()).select_from(runs).where(runs.c.executor_kind==self.executor_kind,runs.c.execution_status.in_(('queued','running')))).scalar_one()
                if pending>=16:raise AuthError('REVIEW_CAPACITY',503,retry_after=1)
                c.execute(insert(runs).values(run_id=run_id,project_id=pid,asset_version_id=image_id,criteria_version_id=version_id,
                    retry_of_run_id=source_id,requested_by=subject.user_id,request_operation=operation,idempotency_key=key,
                    request_hash=request_hash,request_hash_version='cc-request-jcs-v1',origin_kind='service_execution',
                    project_name_snapshot=project['name'],execution_status='queued',current_stage='queued',progress=0,
                    orchestration_mode='single_pipeline',executor_kind=self.executor_kind,workflow_version=self.workflow,
                    aggregation_policy_version=POLICY,execution_target='local',requested_config=config,input_manifest_hash=manifest,
                    manifest_hash_version='cc-manifest-jcs-v1',retry_policy_jsonb=self.retry,available_at=func.clock_timestamp()))
                audit(c,subject,pid,'review.queued','review_run',run_id,request_id)
                result=receipt(one(c,'review_runs','run_id',run_id))
            return result,False
        except PersistenceError as error:
            if error.outcome in ('commit_unknown','committed','rollback_unconfirmed'):
                # No new run/key. A fresh primary + try-lock proves the existing admission.
                with unit_of_work(self.engine) as c:
                    if c.exec_driver_sql('SELECT pg_is_in_recovery()').scalar_one():raise AuthError('DATABASE_UNAVAILABLE',503)
                    if not c.exec_driver_sql('SELECT pg_try_advisory_xact_lock(%s)',(advisory(str(subject.user_id)+operation+str(key)),)).scalar_one():raise AuthError('REVIEW_OUTCOME_UNCONFIRMED',503)
                    old=self.existing(c,subject,dev,operation,key,request_hash)
                    if old:return receipt(old),old['run_id']!=run_id
                raise AuthError('REVIEW_OUTCOME_UNCONFIRMED',503) from None
            raise

    def lock_run(self,c,run_id):
        # The project is immutable; lock it before the run to match guards/account updates.
        row=one(c,'review_runs','run_id',run_id)
        c.exec_driver_sql('SELECT public.cc_lock_project(%s)',(row['project_id'],))
        return one(c,'review_runs','run_id',run_id,lock=True)

    @safe_transaction
    def claim(self,run_id):
        with unit_of_work(self.engine) as c:
            run=self.lock_run(c,run_id)
            if run['execution_status']!='queued' or run['executor_kind']!='background_tasks':return None
            attempt,token=uuid4(),uuid4()
            c.execute(insert(table('review_attempts')).values(attempt_id=attempt,project_id=run['project_id'],asset_version_id=run['asset_version_id'],
                run_id=run_id,attempt_no=1,stage='preprocessing',execution_status='active',dispatch_state='not_dispatched',worker_token=token,
                requested_config=run['requested_config'],started_at=func.clock_timestamp()))
            c.execute(update(table('review_runs')).where(table('review_runs').c.run_id==run_id).values(execution_status='running',current_stage='preprocessing',
                progress=0,active_attempt_id=attempt,started_at=func.clock_timestamp(),available_at=None,updated_at=func.clock_timestamp()))
            system_audit(c,run,'review.started')
            run=one(c,'review_runs','run_id',run_id)
        return run,attempt,token

    def live(self,c,run_id,attempt_id,token):
        run=self.lock_run(c,run_id)
        attempt=one(c,'review_attempts','attempt_id',attempt_id,lock=True)
        if (run['execution_status']!='running' or run['active_attempt_id']!=attempt_id or attempt['worker_token']!=token
            or attempt['execution_status']!='active' or run['executor_kind']!=self.executor_kind):
            raise AuthError('ATTEMPT_AUTHORITY_LOST',409)
        if self.executor_kind=='postgres_worker':
            now=c.exec_driver_sql('SELECT clock_timestamp()').scalar_one()
            if attempt['lease_expires_at'] is None or attempt['lease_expires_at']<=now:
                raise AuthError('ATTEMPT_AUTHORITY_LOST',409)
        return run,attempt

    def dispatch(self,run_id,attempt,token):
        """BackgroundTasks keeps its C10 behavior; durable workers override this boundary."""

    @safe_transaction
    def advance(self,run_id,attempt,token,stage,progress):
        with unit_of_work(self.engine) as c:
            self.live(c,run_id,attempt,token)
            c.execute(update(table('review_runs')).where(table('review_runs').c.run_id==run_id).values(current_stage=stage,progress=progress,updated_at=func.clock_timestamp()))
            c.execute(update(table('review_attempts')).where(table('review_attempts').c.attempt_id==attempt).values(stage=stage))

    @safe_transaction
    def fail(self,run_id,attempt_id=None,token=None,*,code='REVIEW_EXECUTION_FAILED',unknown=False):
        error={'code':code,'message':'검수가 완료되지 않았습니다. 실행 기록을 확인한 뒤 명시적으로 재검수해 주세요.','outcome_unknown':unknown}
        with unit_of_work(self.engine) as c:
            run=self.lock_run(c,run_id)
            if run['execution_status'] in ('completed','failed','imported'):return False
            error['stage']=run['current_stage']
            if attempt_id:
                try:self.live(c,run_id,attempt_id,token)
                except AuthError:return False
                c.execute(update(table('review_attempts')).where(table('review_attempts').c.attempt_id==attempt_id).values(
                    execution_status='unknown' if unknown else 'failed',finished_at=func.clock_timestamp(),error_jsonb=error,retryable=False))
            elif run['active_attempt_id'] is not None:return False
            c.execute(update(table('review_runs')).where(table('review_runs').c.run_id==run_id).values(execution_status='failed',current_stage='failed',
                active_attempt_id=None,available_at=None,finished_at=func.clock_timestamp(),updated_at=func.clock_timestamp(),error_jsonb=error))
            system_audit(c,run,'review.failed')
        return True

    @safe_transaction
    def recover_runs(self):
        # The caller owns the same-root lifetime service lock. Never touch postgres_worker.
        with unit_of_work(self.engine) as c:
            c.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)',(advisory('cc-background-capacity-v1'),))
            if getattr(self,'leader',None) is not None:self.leader.check()
            runs=table('review_runs')
            rows=c.execute(select(runs).where(runs.c.executor_kind=='background_tasks',runs.c.execution_status.in_(('queued','running'))).order_by(runs.c.project_id,runs.c.run_id)).mappings().all()
        for row in rows:
            attempt=row['active_attempt_id'];token=None
            if attempt:
                with unit_of_work(self.engine) as c:token=one(c,'review_attempts','attempt_id',attempt)['worker_token']
            self.fail(row['run_id'],attempt,token,code='RESTART_DURING_EXECUTION' if attempt else 'RESTART_BEFORE_START',unknown=bool(attempt))
        return len(rows)
