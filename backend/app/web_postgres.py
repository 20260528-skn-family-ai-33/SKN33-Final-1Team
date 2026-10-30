"""Current web API facade over the existing M008 business services.

Only an explicit, app-role-only environment file enables this local profile.
Original JSON history is never migrated or rewritten at startup. New JSON files
remain diagnostic copies; admission, state and completed reads use PostgreSQL.
"""
from copy import deepcopy
from io import BytesIO
import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from .config import read_environment_file, ConfigurationError
from .v1_config import load_v1_settings
from .persistence.database import create_app_engine
from .persistence.schema_inventory import check_schema
from .persistence.transaction import unit_of_work
from .services.business import Business, table, one, timestamp
from .services.files import Files
from .services.reviews import Reviews
from .services.review_results import ReviewExecutor
from .contracts.business import ProjectCreate, SetCreate, DraftCreate, Confirm
from .security.errors import AuthError


class WebPipeline:
    def describe(self):
        from .api import _runtime_identity
        identity=_runtime_identity()
        if identity['execution_target']!='local':raise AuthError('REVIEW_RUNTIME_UNAVAILABLE',503)
        names=('api.py','structured_review.py','document_conditions.py','web_postgres.py')
        revision=hashlib.sha256(b''.join((Path(__file__).parent/name).read_bytes() for name in names)).hexdigest()
        return dict(pipeline='cc-current-web-v1',ocr_pipeline=identity.get('pipeline'),execution_target='local',
                    code_revision=revision,preprocessing={'version':'cc-display-2x-lanczos-v1','ocr_scale':2})


class CompletedWebPipeline:
    """Normalize already executed output; this adapter never calls a model."""
    def __init__(self,config,payload):self.config,self.payload=config,payload
    def describe(self):return self.config
    def recognize(self,path,directory):
        records=deepcopy(self.payload['ocr']['records'])
        for record in records:
            if record.get('bbox_xyxy') is not None:record['bbox_xyxy']=[v*2 for v in record['bbox_xyxy']]
            if record.get('polygon') is not None:record['polygon']=[[v*2 for v in point] for point in record['polygon']]
        return dict(status='success',engine=self.payload['runtime'].get('engine'),pipeline=self.config['pipeline'],
                    device='cpu',execution_target='local',input_dimensions=self.payload['ocr']['input_dimensions'],records=records)
    def check(self,criteria,records):
        from .event_layout import extract_event_multiline
        import json
        event=json.loads(json.dumps(extract_event_multiline(records),ensure_ascii=False))
        return self.payload['results'],event


class CompletedWebExecutor(ReviewExecutor):
    def __init__(self,reviews,leader,payload,config):
        super().__init__(reviews,leader);self.payload=payload
        self.adapter=CompletedWebPipeline(config,payload)
    def advance(self,*args):
        # Inference and its stage updates already happened in the current web API.
        pass
    def augment_plan(self,plan):
        if self.payload['runtime'].get('device')!='cpu':raise ValueError('Unsupported web OCR runtime')
        if self.payload['runtime'].get('execution_target')!='local':raise ValueError('Unsupported web OCR target')
        plan['runtime']={**self.payload['runtime'],'model_runtime':self.payload.get('model_runtime'),'status':'completed'}
        plan['ocr_metadata']={'web_ocr':self.payload['ocr']}
        raw={item['criterion_id']:item for item in self.payload['results']}
        # Preserve model_analysis, metadata_checks and all additional source fields.
        with unit_of_work(self.engine) as c:
            criteria=c.execute(select(table('criteria_items')).where(table('criteria_items').c.criteria_version_id==UUID(plan['criteria_version_id']))).mappings().all()
        external={str(row['criteria_item_id']):row['external_item_id'] for row in criteria}
        plan['web_source_results']={item['criteria_item_id']:raw[external[item['criteria_item_id']]] for item in plan['items']}


class PostgresWebStore:
    def __init__(self,settings,*,engine=None):
        if settings.environment not in ('development','test') or settings.secure:
            raise ConfigurationError('The current web PostgreSQL facade is local development only.')
        if not settings.storage_root or settings.review_executor!='background_tasks':
            raise ConfigurationError('Current web PostgreSQL requires private storage and BackgroundTasks.')
        self.settings=settings;self.engine=engine;self.owned=engine is None
        self.storage=None;self.leader=None;self.claims={}

    @classmethod
    def configured(cls,repo):
        selected=os.environ.get('CC_WEB_POSTGRES_ENV_FILE')
        path=Path(selected) if selected else repo/'.runtime/postgres-web.env'
        if not selected and not path.exists():return None
        return cls(load_v1_settings(read_environment_file(path)))

    def open(self,app):
        from .storage.local import LocalStorage
        from .execution.leader import Leader
        from .security.passwords import Passwords
        from .security.csrf import Csrf
        from .security.limits import LoginLimits
        from .services.auth import AuthService
        from .services.authorization import Authorization
        self.engine=self.engine or create_app_engine(self.settings)
        check_schema(self.engine,self.settings)
        self.storage=LocalStorage(self.settings.storage_root)
        self.leader=Leader(self.engine)
        app.state.settings=self.settings;app.state.engine=self.engine
        app.state.csrf=Csrf(self.settings.csrf_secret)
        app.state.auth=AuthService(self.engine,self.settings,Passwords(),LoginLimits(self.settings.csrf_secret))
        authorization=Authorization(self.settings)
        self.business=Business(self.engine,authorization,self.settings)
        self.files=Files(self.engine,self.storage,self.business)
        self.reviews=Reviews(self.engine,self.business,self.storage,WebPipeline())
        self.reviews.leader=self.leader
        self.reviews.recover_runs()

    def close(self):
        if self.leader:self.leader.close();self.leader=None
        if self.storage:self.storage.close();self.storage=None
        if self.owned and self.engine:self.engine.dispose()

    def admit(self,subject,dev,data,snapshot,queued,request_id):
        self.leader.check()
        pid=UUID(self.business.create_project(subject,dev,ProjectCreate(name=snapshot['project_name']),request_id)['id'])
        image=self.files.upload(subject,dev,pid,BytesIO(data),request_id)
        criteria_set=self.business.create_set(subject,dev,pid,SetCreate(name='웹에서 확정한 검수 기준'),request_id)
        items=[{**item,'selected':True} for item in snapshot['criteria']]
        draft=self.business.create_draft(subject,dev,pid,DraftCreate(criteria_set_id=criteria_set['id'],image_id=image['id'],
            raw_requirements='\n'.join(item['text'] for item in items),items=items),request_id)
        version,_=self.business.confirm(subject,dev,pid,Confirm(draft_id=draft['id'],expected_revision=draft['revision']),uuid4(),request_id)
        # The immutable input snapshot retains browser version, PDF metadata and image metadata.
        source={**queued,'_web_snapshot':snapshot}
        admitted,_=self.reviews.admit(subject,dev,dict(project_id=str(pid),image_id=image['id'],criteria_version_id=version['id']),
                                     uuid4(),request_id,source_snapshot=source)
        return admitted['id']

    def claim(self,identifier):
        self.leader.check()
        claimed=self.reviews.claim(UUID(identifier))
        if not claimed:raise AuthError('REVIEW_NOT_QUEUED',409)
        self.claims[identifier]=claimed

    def update(self,identifier,payload):
        self.leader.check()
        run,attempt,token=self.claims[identifier]
        if payload['status']=='completed':
            executor=CompletedWebExecutor(self.reviews,self.leader,payload,run['requested_config'])
            executor.execute_claimed((run,attempt,token))
            with unit_of_work(self.engine) as c:actual=one(c,'review_runs','run_id',UUID(identifier))
            if actual['execution_status']!='completed':raise AuthError('REVIEW_PERSIST_FAILED',503)
        elif payload['status']=='failed':
            self.reviews.fail(UUID(identifier),attempt,token,code='WEB_REVIEW_EXECUTION_FAILED')
        else:
            self.reviews.advance(UUID(identifier),attempt,token,payload['stage'],min(payload['progress'],99))

    def read(self,identifier,subject,dev):
        try:rid=UUID(identifier)
        except ValueError:raise AuthError('RESOURCE_NOT_FOUND',404) from None
        with unit_of_work(self.engine) as c:
            existing=c.execute(select(table('review_runs')).where(table('review_runs').c.run_id==rid)).mappings().one_or_none()
            if existing is None:return None
            run=self.business.authorization.resource(c,subject,'review_runs',rid,developer_allowed=dev)
            if run['origin_kind']!='service_execution':return None
            source=deepcopy(run['source_snapshot_jsonb'] or {})
            if '_web_snapshot' not in source:return None
            source.pop('_web_snapshot');source['id']=identifier
            source['image']['url']=f'/api/reviews/{identifier}/image'
            source.update(status=run['execution_status'],stage=run['current_stage'],progress=run['progress'],
                          updated_at=timestamp(run['updated_at']),error=run['error_jsonb']['message'] if run['error_jsonb'] else None)
            if run['selected_attempt_id']:
                attempt=one(c,'review_attempts','attempt_id',run['selected_attempt_id'])
                runtime=dict(attempt['actual_runtime'] or {});source['model_runtime']=runtime.pop('model_runtime',None)
                source['runtime']=runtime
                results=table('item_results');criteria=table('criteria_items')
                source['results']=[row[0] for row in c.execute(select(results.c.source_result_jsonb).join(criteria,
                    criteria.c.criteria_item_id==results.c.criteria_item_id).where(results.c.run_id==rid,
                    results.c.attempt_id==run['selected_attempt_id']).order_by(criteria.c.position))]
                ocr=c.execute(select(table('ocr_runs').c.runtime_metadata).where(table('ocr_runs').c.attempt_id==run['selected_attempt_id'])).scalar_one()
                source['ocr']=ocr['web_ocr']
            return source

    def image(self,identifier,subject,dev,*,original=False):
        try:rid=UUID(identifier)
        except ValueError:raise AuthError('RESOURCE_NOT_FOUND',404) from None
        with unit_of_work(self.engine) as c:
            row=c.execute(select(table('review_runs')).where(table('review_runs').c.run_id==rid)).mappings().one_or_none()
            if row is None:return None
            run=self.business.authorization.resource(c,subject,'review_runs',rid,developer_allowed=dev)
            image=one(c,'asset_versions','asset_version_id',run['asset_version_id'])
            file=one(c,'stored_files','file_id',image['original_file_id' if original else 'display_file_id'])
        with self.storage.verified(file['storage_key'],file['byte_size'],file['sha256']):pass
        return self.storage.path/file['storage_key']
