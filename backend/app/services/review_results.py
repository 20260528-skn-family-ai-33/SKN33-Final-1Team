"""Immutable prepared graph, pre-published files, token fenced atomic result commit."""
from io import BytesIO
import hashlib
import json
import threading
from uuid import UUID,uuid4
from PIL import Image
from sqlalchemy import select,insert,update,func
from ..execution import output
from ..hashing.canonical import digest,canonical
from ..persistence.transaction import unit_of_work
from ..persistence.database import PersistenceError
from ..storage.local import key,operation_key,StorageError
from ..security.errors import AuthError
from .business import table,one
from .reviews import POLICY,system_audit


class ResultUnconfirmed(RuntimeError):pass


class ReviewExecutor:
    def __init__(self,reviews,leader):
        self.reviews=reviews;self.engine=reviews.engine;self.storage=reviews.storage;self.adapter=reviews.adapter;self.leader=leader
        self.slot=threading.BoundedSemaphore(1)
        self.last_success_config=None
        self.pending=set()
        self.pending_lock=threading.Lock()

    def execute(self,run_id):
        """Deduplicate queued delivery before waiting for the single inference slot."""
        with self.pending_lock:
            if run_id in self.pending:return
            self.pending.add(run_id)
        try:self._execute_owned(run_id)
        finally:
            with self.pending_lock:self.pending.discard(run_id)

    def _execute_owned(self,run_id):
        """Starlette calls this after sending the committed admission response."""
        with self.slot:
            try:
                self.leader.check()
                claimed=self.reviews.claim(run_id)
                if claimed:self.execute_claimed(claimed)
            except Exception:pass

    def execute_claimed(self,claimed):
        """One already-committed claim; never reclaim or rerun inference after uncertainty."""
        run,attempt,token=claimed
        run_id=run['run_id']
        try:
            self.leader.check()
            with self.storage.lock('operation-'+str(attempt)):
                plan,intent=self.prepare(run,attempt,token)
                self.leader.check()
                with self.storage.lock('gate',shared=True):
                    for item in intent['files']:self.storage.publish(intent['operation_id'],item)
                    intent.update(phase='finalized',db_outcome='unconfirmed');self.storage.journal(intent)
                    try:self.persist(plan,token)
                    except PersistenceError as error:
                        state=self.resolve(intent)
                        if state=='committed':pass
                        else:
                            intent['db_outcome']=state;self.storage.journal(intent)
                            if state=='not_committed' and error.outcome=='rolled_back':raise
                            raise ResultUnconfirmed() from None
                    intent['db_outcome']='committed';self.storage.journal(intent)
                    self.last_success_config=run['requested_config']
        except ResultUnconfirmed:return
        except Exception as error:
            # Raw exception messages may contain paths, driver details or credentials.
            try:
                self.leader.check()
                code='OUTPUT_CONTRACT_INVALID' if isinstance(error,(ValueError,output.OutputError)) else 'REVIEW_EXECUTION_FAILED'
                self.reviews.fail(run_id,attempt,token,code=code)
            except Exception:pass

    def diagnostic(self,path,value):
        try:
            raw=json.dumps(value,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode()
            if len(raw)>output.MAX_RAW:raw=b'{"diagnostic":"raw_output_exceeded_limit"}'
        except (ValueError,TypeError,RecursionError):raw=b'{"diagnostic":"raw_output_not_json"}'
        self.storage.write_new(path,raw)

    def advance(self,run_id,attempt,token,stage,progress):
        self.reviews.advance(run_id,attempt,token,stage,progress)

    def augment_plan(self,plan):
        """Adapters may preserve additional source JSON before the plan is frozen."""

    def prepare(self,run,attempt,token):
        with unit_of_work(self.engine) as c:
            image=one(c,'asset_versions','asset_version_id',run['asset_version_id'])
            file=one(c,'stored_files','file_id',image['display_file_id'])
            frames=table('image_coordinate_frames')
            display_frame=c.execute(select(frames).where(frames.c.asset_version_id==run['asset_version_id'],frames.c.frame_kind=='display')).mappings().one()
            criteria=c.execute(select(table('criteria_items')).where(table('criteria_items').c.criteria_version_id==run['criteria_version_id']).order_by(table('criteria_items').c.position)).mappings().all()
        if self.adapter.describe()!=run['requested_config']:raise output.OutputError('Execution configuration changed')
        width,height=image['display_width'],image['display_height']
        with self.storage.verified(file['storage_key'],file['byte_size'],file['sha256']) as stream:
            with Image.open(stream) as opened:
                opened.load();resized=opened.resize((width*2,height*2),Image.Resampling.LANCZOS)
                encoded=BytesIO();resized.save(encoded,format='PNG',compress_level=6,optimize=False);input_bytes=encoded.getvalue()
        if len(input_bytes)>160*1024*1024:raise output.OutputError('Input too large')
        op=str(attempt);input_id=str(uuid4());stage='staging/'+op
        self.storage.write_new(stage+'/'+input_id,input_bytes)
        self.leader.check()
        self.advance(run['run_id'],attempt,token,'ocr',30)
        # The path is built from anchored, exclusively created server IDs and private directories.
        input_path=self.storage.path/stage/input_id
        self.leader.check()
        self.reviews.dispatch(run['run_id'],attempt,token)
        self.leader.check()
        payload=self.adapter.recognize(input_path,self.storage.path/stage/'model-output')
        if self.adapter.describe()!=run['requested_config']:raise output.OutputError('Execution configuration changed')
        self.diagnostic(stage+'/ocr.received.json',payload)
        records=output.normalize(payload,width,height)
        if payload.get('pipeline')!=run['requested_config']['pipeline']:raise output.OutputError('Pipeline mismatch')
        self.leader.check()
        self.advance(run['run_id'],attempt,token,'review',80)
        checker_records=[{k:v for k,v in record.items() if k in ('id','text','confidence','bbox_xyxy','polygon','recognition_engine','requires_review')}
                         for record in records]
        # Preserve candidates/provenance needed by the existing conservative checker.
        for result,record in zip(checker_records,records):
            for name in ('baseline_text','alternatives','baseline_confidence','baseline_recognition_engine','baseline_requires_review','selection_reason','recognition_status'):
                if name in record['source_record']:result[name]=record['source_record'][name]
        raw_results,event=self.adapter.check([dict(id=item['external_item_id'],text=item['text'],type=item['category']) for item in criteria],checker_records)
        self.diagnostic(stage+'/checker.received.json',raw_results)
        self.diagnostic(stage+'/extraction.received.json',event)
        frame_id=str(display_frame['frame_id'])
        items=output.results(criteria,raw_results,records,frame_id);observations=output.observations(event,records)
        token_map={record['token_id']:record for record in records}
        for observation in observations:
            observation['evidence']=[]
            for token_id in observation['token_ids']:
                record=token_map[token_id]
                observation['evidence'].append(dict(id=str(uuid4()),source_kind='ocr',text=record['text'],bbox_xyxy=record['bbox_xyxy'],polygon=record['polygon'],
                    confidence=record['confidence'],frame_id=frame_id,coordinate_space='normalized_original_pixels',token_ids=[token_id],method_code='field_ocr_source',limitations=None))
            observation['evidence'].sort(key=lambda value:value['id'])
        ocr_id,extraction_id,input_frame_id=map(str,(uuid4(),uuid4(),uuid4()))
        files=[]
        for role,data,file_id in [('ocr_input',input_bytes,input_id),('source_json',output.raw_json(payload),str(uuid4())),
                                  ('source_json',output.raw_json(event),str(uuid4())),('source_json',output.raw_json(raw_results),str(uuid4()))]:
            files.append(dict(file_id=file_id,project_id=str(run['project_id']),file_role=role,storage_key=key(run['project_id'],file_id),
                              byte_size=str(len(data)),sha256=hashlib.sha256(data).hexdigest()))
            if file_id!=input_id:self.storage.write_new(stage+'/'+file_id,data)
        overall=output.aggregate(items)
        manifest=dict(run_id=str(run['run_id']),selected_attempt_id=str(attempt),execution_status='completed',overall_verdict=overall,
            aggregation_policy_version=POLICY,input_manifest_hash=run['input_manifest_hash'],
            ocr=dict(ocr_run_id=ocr_id,ocr_input_sha256=files[0]['sha256'],source_json_sha256=files[1]['sha256'],records=records),
            extraction=dict(extraction_id=extraction_id,source_json_sha256=files[2]['sha256'],observations=observations),checker_source_sha256=files[3]['sha256'],items=items)
        result_hash=digest('review_result',manifest,'cc-manifest-jcs-v1')
        plan=dict(run_id=str(run['run_id']),attempt_id=str(attempt),project_id=str(run['project_id']),image_id=str(run['asset_version_id']),
            criteria_version_id=str(run['criteria_version_id']),input_manifest_hash=run['input_manifest_hash'],files=files,
            display_file_id=str(file['file_id']),display_frame_id=frame_id,input_frame_id=input_frame_id,width=width,height=height,
            ocr_id=ocr_id,extraction_id=extraction_id,records=records,event=event,observations=observations,items=items,
            result_manifest_hash=result_hash,overall_verdict=overall,runtime=dict(engine=payload.get('engine'),pipeline=payload['pipeline'],device='cpu',execution_target='local',status='completed'))
        self.augment_plan(plan)
        frozen=canonical(plan)
        if len(frozen)>48*1024*1024:raise output.OutputError('Prepared graph too large')
        self.storage.write_new(stage+'/plan.json',frozen)
        intent=dict(contract_version='cc-storage-journal-v1',operation='review_result',operation_id=op,project_id=plan['project_id'],
            image_id=plan['image_id'],run_id=plan['run_id'],attempt_id=str(attempt),ocr_id=ocr_id,extraction_id=extraction_id,
            result_manifest_hash=result_hash,plan_sha256=hashlib.sha256(frozen).hexdigest(),files=files,phase='staged',db_outcome='not_started')
        self.storage.journal(intent)
        return plan,intent

    def persist(self,plan,token):
        for attempt in range(3):
            try:
                self.leader.check()
                with unit_of_work(self.engine) as c:
                    c.exec_driver_sql('SELECT pg_advisory_xact_lock(%s)',(operation_key(plan['attempt_id']),))
                    run,_=self.reviews.live(c,UUID(plan['run_id']),UUID(plan['attempt_id']),token)
                    pid,image_id,attempt_id=map(UUID,(plan['project_id'],plan['image_id'],plan['attempt_id']))
                    common=dict(project_id=pid,asset_version_id=image_id)
                    for index,item in enumerate(plan['files']):
                        c.execute(insert(table('stored_files')).values(file_id=UUID(item['file_id']),project_id=pid,file_role=item['file_role'],
                            storage_key=item['storage_key'],byte_size=int(item['byte_size']),sha256=item['sha256'],
                            parent_file_id=UUID(plan['display_file_id']) if index==0 else None,
                            detected_mime='image/png' if index==0 else 'application/json',created_by=None,
                            source_metadata={'operation_id':plan['attempt_id'],'run_id':plan['run_id']}))
                    c.execute(insert(table('image_coordinate_frames')).values(frame_id=UUID(plan['input_frame_id']),**common,
                        file_id=UUID(plan['files'][0]['file_id']),parent_frame_id=UUID(plan['display_frame_id']),frame_kind='ocr_input',
                        coordinate_space='ocr_input_pixels',width=plan['width']*2,height=plan['height']*2,transform_jsonb={'scale':2,'method':'LANCZOS'}))
                    ocr_id=UUID(plan['ocr_id']);extraction_id=UUID(plan['extraction_id'])
                    c.execute(insert(table('ocr_runs')).values(ocr_run_id=ocr_id,**common,attempt_id=attempt_id,
                        source_file_id=UUID(plan['files'][1]['file_id']),input_file_id=UUID(plan['files'][0]['file_id']),
                        frame_id=UUID(plan['display_frame_id']),execution_status='completed',engine_name=plan['runtime']['engine'],device='cpu',
                        execution_target='local',pipeline_name=plan['runtime']['pipeline'],pipeline_version=run['workflow_version'],
                        reported_coordinate_space='ocr_input_pixels',coordinate_space='normalized_original_pixels',
                        runtime_metadata={'input_dimensions':[plan['width']*2,plan['height']*2],'scale':2,**plan.get('ocr_metadata',{})},started_at=func.clock_timestamp(),completed_at=func.clock_timestamp()))
                    for record in plan['records']:
                        c.execute(insert(table('ocr_tokens')).values(token_id=UUID(record['token_id']),**common,ocr_run_id=ocr_id,
                            **{k:record[k] for k in ('external_token_id','external_token_type','token_order','text','confidence','bbox_xyxy','polygon','recognition_engine','requires_review')},source_record_jsonb=record['source_record']))
                    c.execute(update(table('ocr_runs')).where(table('ocr_runs').c.ocr_run_id==ocr_id).values(sealed_at=func.clock_timestamp()))
                    extractions=table('poster_extractions')
                    number=c.execute(select(func.coalesce(func.max(extractions.c.version_no),0)).where(extractions.c.asset_version_id==image_id)).scalar_one()+1
                    c.execute(insert(extractions).values(extraction_id=extraction_id,**common,ocr_run_id=ocr_id,source_file_id=UUID(plan['files'][2]['file_id']),
                        version_no=number,method='conservative_ocr_rules',extractor_version='cc-event-v1',payload_jsonb=plan['event'],
                        compatibility_state='native',validation_state='schema_unavailable',checks_jsonb={'external_schema':'unavailable'},projection_version='cc-event-projection-v1'))
                    tokens={record['token_id']:record for record in plan['records']}
                    def evidence(value,*,result=None,observation=None):
                        eid=UUID(value['id'])
                        c.execute(insert(table('evidence_records')).values(evidence_id=eid,**common,item_result_id=result,observation_id=observation,
                            source_ocr_run_id=ocr_id,frame_id=UUID(plan['display_frame_id']),source_kind='ocr',text=value['text'],
                            bbox_xyxy=value['bbox_xyxy'],polygon=value['polygon'],confidence=value['confidence'],method_code=value['method_code'],limitations_jsonb=value.get('limitations')))
                        for order,token_id in enumerate(value['token_ids']):
                            c.execute(insert(table('evidence_ocr_tokens')).values(evidence_id=eid,token_id=UUID(token_id),**common,ocr_run_id=ocr_id,token_order=order))
                    for observation in plan['observations']:
                        oid=UUID(observation['observation_id'])
                        c.execute(insert(table('poster_field_observations')).values(observation_id=oid,**common,extraction_id=extraction_id,
                            **{k:observation[k] for k in ('field_code','field_path','presence_status','resolution_status','raw_text','normalized_value','source_status','source_reason')},adapter_version='cc-event-projection-v1'))
                        for value in observation['evidence']:evidence(value,observation=oid)
                    c.execute(update(extractions).where(extractions.c.extraction_id==extraction_id).values(sealed_at=func.clock_timestamp()))
                    for item in plan['items']:
                        rid=UUID(item['result_id']);criterion=UUID(item['criteria_item_id'])
                        c.execute(insert(table('item_results')).values(result_id=rid,**common,run_id=run['run_id'],attempt_id=attempt_id,
                            criteria_version_id=run['criteria_version_id'],criteria_item_id=criterion,ocr_run_id=ocr_id,extraction_id=extraction_id,
                            result_origin='service_execution',execution_status=item['execution_status'],verdict=item['verdict'],observed_value=item['observed_value'],
                            reason=item['reason'],method_code=item['method_code'],checker_version=item['checker_version'],input_manifest_hash=run['input_manifest_hash'],limitations_jsonb=item['limitations'],source_result_jsonb=plan.get('web_source_results',{}).get(item['criteria_item_id'],item.get('source_result_jsonb'))))
                        for value in item['evidence']:evidence(value,result=rid)
                        c.execute(insert(table('run_item_selections')).values(run_id=run['run_id'],criteria_item_id=criterion,result_id=rid,**common,
                            criteria_version_id=run['criteria_version_id'],selection_kind='pipeline_final'))
                    c.execute(update(table('review_attempts')).where(table('review_attempts').c.attempt_id==attempt_id).values(execution_status='succeeded',stage='review',
                        actual_runtime=plan['runtime'],finished_at=func.clock_timestamp(),retryable=False))
                    c.execute(update(table('review_runs')).where(table('review_runs').c.run_id==run['run_id']).values(execution_status='completed',current_stage='completed',
                        progress=100,active_attempt_id=None,selected_attempt_id=attempt_id,overall_verdict=plan['overall_verdict'],result_manifest_hash=plan['result_manifest_hash'],
                        finished_at=func.clock_timestamp(),sealed_at=func.clock_timestamp(),updated_at=func.clock_timestamp()))
                    system_audit(c,run,'review.completed')
                return
            except PersistenceError as error:
                if error.retryable and attempt<2:continue
                raise

    def resolve(self,intent):
        try:
            with unit_of_work(self.engine) as c:
                if c.exec_driver_sql('SELECT pg_is_in_recovery()').scalar_one():return 'unresolved'
                if not c.exec_driver_sql('SELECT pg_try_advisory_xact_lock(%s)',(operation_key(intent['operation_id']),)).scalar_one():return 'unresolved'
                run=one(c,'review_runs','run_id',UUID(intent['run_id']))
                files=[c.execute(select(table('stored_files')).where(table('stored_files').c.file_id==UUID(item['file_id']))).mappings().one_or_none() for item in intent['files']]
                if run['execution_status']=='completed':
                    if (run['result_manifest_hash']!=intent['result_manifest_hash'] or str(run['selected_attempt_id'])!=intent['attempt_id'] or not run['sealed_at']):return 'conflict'
                    if any(row is None for row in files):return 'conflict'
                    for expected,actual in zip(intent['files'],files):
                        if any(str(actual[k])!=expected[k] for k in ('file_id','project_id','file_role','storage_key','byte_size','sha256')):return 'conflict'
                    return 'committed'
                if any(row is not None for row in files):return 'conflict'
                return 'not_committed'
        except (PersistenceError,AuthError):return 'unresolved'

    def recover(self,*,concurrent=False):
        result={}
        with self.storage.recovery_lock('gate',concurrent=concurrent) as locked:
            if not locked:return result
            for intent in self.storage.journals():
                if intent['operation']!='review_result':continue
                with self.storage.recovery_lock('operation-'+intent['operation_id'],concurrent=concurrent) as locked:
                    if not locked:continue
                    for item in intent['files']:
                        try:
                            with self.storage.read(item['storage_key'],links=2):pass
                        except (FileNotFoundError,StorageError):continue
                        else:self.storage.publish(intent['operation_id'],item)
                    state=self.resolve(intent)
                    if state in ('conflict','unresolved'):raise StorageError('STORAGE_RECOVERY_REQUIRED')
                    if state=='committed':
                        for item in intent['files']:
                            with self.storage.verified(item['storage_key'],int(item['byte_size']),item['sha256']):pass
                    intent['db_outcome']=state;self.storage.journal(intent);result[intent['operation_id']]=state
        return result
