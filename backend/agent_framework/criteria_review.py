"""Immutable criteria revisions and explicit approvals over the existing DB.

No model calls or implicit approvals. File checks happen outside DB transactions;
project/run locks serialize optimistic version and approval commands.
"""
from hashlib import sha256
from uuid import UUID,uuid5,NAMESPACE_URL

from sqlalchemy import BigInteger,Boolean,Column,MetaData,String,Table,insert,select,func
from sqlalchemy.dialects.postgresql import JSONB,TIMESTAMP,UUID as PGUUID

from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from .contracts import Approval,Context,Criterion,CriteriaValueSource,DocumentRef,EvidenceBundle,RunSnapshot,RequirementDetails
from .criteria_contracts import (CriteriaAddition, CriteriaAdditionRequest, CriteriaApprovalRecord,CriteriaApprovalRequest,
    CriteriaVersion, CriteriaVersionRecord,CriteriaVersionRequest,RecordedCriteriaEdit)
from .context import freeze_approved_context
from .documents import versions as documents
from .postgres import approvals,runs,steps,payload,digest,extraction_run_id
from .validation import _criteria,_number,validate_draft,validate_evidence

metadata=MetaData(schema='agent_framework')
versions=Table('criteria_versions',metadata,*(Column(n,t) for n,t in (
    ('criteria_version_id',PGUUID(as_uuid=True)),('source_run_id',String),('project_id',PGUUID(as_uuid=True)),
    ('document_id',PGUUID(as_uuid=True)),('document_version_id',PGUUID(as_uuid=True)),('parent_version_id',PGUUID(as_uuid=True)),
    ('version_no',BigInteger),('request_key',PGUUID(as_uuid=True)),('request_json',JSONB),('request_sha256',String),
    ('snapshot_json',JSONB),('snapshot_sha256',String),('created_by',PGUUID(as_uuid=True)),('created_at',TIMESTAMP(timezone=True)))))
bindings=Table('criteria_approval_bindings',metadata,*(Column(n,t) for n,t in (
    ('approval_id',PGUUID(as_uuid=True)),('source_run_id',String),('project_id',PGUUID(as_uuid=True)),
    ('criteria_version_id',PGUUID(as_uuid=True)),('criteria_sha256',String),('request_key',PGUUID(as_uuid=True)),
    ('request_json',JSONB),('request_sha256',String),('evidence_json',JSONB),('synthetic',Boolean))))


def _error(code,status=409):raise AuthError(code,status)


def approval_blockers(criteria):
    result={}
    supported={'text':{'equals','contains'},'number':{'equals','gte','lte'},'visual':{'present'}}
    for criterion in criteria:
        issues=[]
        if criterion.operator not in supported.get(criterion.kind,set()):issues.append('unsupported_or_manual_criterion')
        if not criterion.expected.strip():issues.append('missing_expected_value')
        if criterion.kind=='number' and criterion.expected.strip():
            try:
                _number(criterion.expected,criterion.unit)
            except ValueError:issues.append('numeric_expected_invalid')
        if criterion.human_added and criterion.value_source is None:issues.append('missing_value_source')
        if criterion.details is not None and criterion.details.target=='unspecified':issues.append('missing_inspection_target')
        if issues:result[criterion.criterion_id]=tuple(issues)
    return result


def direct_input_quote(quote, text):
    """Include the exact typed line(s) around an unambiguous A02 citation."""
    start = text.find(quote)
    if start < 0:
        raise ValueError('direct input citation is missing')
    if text.find(quote, start + 1) >= 0:
        return text
    first_line = text.rfind('\n', 0, start) + 1
    end = text.find('\n', start + len(quote))
    return text[first_line:end if end >= 0 else len(text)].rstrip('\r')


def revise_criteria(draft,parent,request,*,version_id,created_by,created_at,addition=None):
    """Pure revision logic. Preserves the original draft and PDF anchors across versions."""
    originals={c.criterion_id:c for c in draft.criteria}
    current={c.criterion_id:c for c in (parent.criteria if parent else draft.criteria)}
    originals.update((cid, criterion) for cid, criterion in current.items() if criterion.source.kind == 'direct_input')
    excluded={c.criterion_id:c for c in parent.exclusions} if parent else {}
    synthetic=draft.synthetic or bool(parent and parent.synthetic) or request.purpose=='synthetic_test'
    addition_run_ids = parent.addition_run_ids if parent else ()
    if addition is not None:
        admission, extracted = addition
        synthetic = synthetic or extracted.synthetic
        addition_run_ids += (extracted.context.run_id,)
    changes=[]

    def value_source(cid, reference, quote):
        return CriteriaValueSource(record_id=str(uuid5(NAMESPACE_URL,str(version_id)+':'+cid)),
            purpose=request.purpose,reference=reference,quote=quote,quote_sha256=sha256(quote.encode()).hexdigest(),
            submitted_by=str(created_by),submitted_at=created_at,synthetic=synthetic)

    for edit in request.edits:
        if edit.criterion_id not in originals:raise ValueError('unknown criterion')
        source=value_source(edit.criterion_id,edit.source.reference,edit.source.quote)
        change=RecordedCriteriaEdit(criterion_id=edit.criterion_id,action=edit.action,reason=edit.reason,source=source)
        changes.append(change)
        if edit.action=='exclude':
            if edit.criterion_id not in current:raise ValueError('criterion is already excluded')
            current.pop(edit.criterion_id);excluded[edit.criterion_id]=change
        else:
            original=originals[edit.criterion_id]
            previous=current.get(edit.criterion_id,original)
            details=previous.details
            scope=({'target':edit.resolution.target,'field':edit.resolution.field} if edit.resolution.target is not None else {})
            if details is not None or scope:
                details=RequirementDetails(**((details.model_dump(mode='python') if details is not None else {})|
                    scope|{'expected_source':None,'normalized_expected':None,'presentation':None}|
                    ({'qualifiers':()} if edit.resolution.replacement_text is not None else {})))
            current[edit.criterion_id]=Criterion(**(original.model_dump(mode='python')|
                edit.resolution.model_dump(mode='python',exclude={'target','field','replacement_text'})|
                {'text':edit.resolution.replacement_text or previous.text,'human_added':True,'value_source':source,'details':details}))
            excluded.pop(edit.criterion_id,None)
    if addition is not None:
        for cid, criterion in tuple(current.items()):
            if admission.criterion_ids is None or criterion.source.kind == 'direct_input' or cid in admission.criterion_ids:
                continue
            reason = '자료 입력에서 검수 대상으로 선택하지 않았습니다.'
            change = RecordedCriteriaEdit(criterion_id=cid, action='exclude', reason=reason,
                source=value_source(cid, '문서 검수 항목 선택', reason))
            excluded[cid] = change
            changes.append(change)
            del current[cid]
        text = extracted.extraction.sources[0].text
        for criterion in extracted.criteria:
            cid = f'input-{extracted.context.run_id}-{criterion.criterion_id}'
            if cid in originals:
                raise ValueError('duplicate added criterion')
            quote = direct_input_quote(criterion.source.quote, text)
            source = criterion.source.model_copy(update={'kind': 'direct_input', 'quote': quote,
                'source_hash': sha256(quote.encode()).hexdigest()})
            value = value_source(cid, '담당자 직접 입력', criterion.source.quote)
            details = criterion.details
            if details is not None:
                expected = details.expected_source
                details = details.model_copy(update={
                    'expected_source': expected.model_copy(update={'kind': 'direct_input'}) if expected else None,
                    'qualifiers': tuple(ref.model_copy(update={'kind': 'direct_input'}) for ref in details.qualifiers),
                })
            current[cid] = criterion.model_copy(update={'criterion_id': cid, 'source': source,
                'human_added': True, 'value_source': value, 'details': details})
            originals[cid] = current[cid]
            changes.append(RecordedCriteriaEdit(criterion_id=cid, action='add', reason='담당자 직접 입력', source=value))
    if not current:raise ValueError('cannot exclude all criteria')
    criteria=tuple(current[cid] for cid in originals if cid in current)
    context=draft.context.model_copy(update={'criteria_version_id':str(version_id)})
    _criteria(context,criteria)
    return CriteriaVersion(criteria_version_id=version_id,source_run_id=context.run_id,context=context,
        parent_version_id=parent.criteria_version_id if parent else None,version_no=parent.version_no+1 if parent else 1,
        source_draft_sha256=digest(draft),document_sha256=draft.extraction.document_sha256,
        criteria=criteria,exclusions=tuple(excluded.values()),changes=tuple(changes),created_by=str(created_by),
        created_at=created_at,purpose=request.purpose,synthetic=synthetic,addition_run_ids=addition_run_ids)


class PostgresCriteriaReview:
    def __init__(self,store,document_service=None):
        self.store,self.engine,self.documents=store,store.engine,document_service

    def _open(self,c,subject,run_id,*,write=False):
        row=self.store._row(c,run_id)
        snapshot=RunSnapshot.model_validate(row['snapshot_json'])
        self.store._access(c,subject,snapshot.context,write=write)
        if write:row=self.store._row(c,run_id,lock=True);snapshot=RunSnapshot.model_validate(row['snapshot_json'])
        if row['input_json'].get('criteria_addition') is not None:
            _error('CRITERIA_ADDITION_REQUIRES_SOURCE_RUN')
        if row['input_json']['asset'] is not None or snapshot.context.asset_version_id is not None:
            _error('CRITERIA_REVIEW_REQUIRES_PDF_ONLY')
        if snapshot.criteria is None or snapshot.criteria.extraction is None:_error('CRITERIA_DRAFT_NOT_READY')
        if snapshot.execution_status not in ('waiting_criteria','waiting_asset'):_error('CRITERIA_DRAFT_NOT_READY')
        return row,snapshot

    def preflight(self,subject,run_id):
        with unit_of_work(self.engine) as c:self._open(c,subject,run_id,write=True)

    def _verify_file(self,subject,run_id):
        if self.documents is None:_error('PDF_REGISTRATION_DISABLED',503)
        snapshot=self.store.get(subject,run_id)
        self.documents.content(subject,False,UUID(snapshot.context.project_id),UUID(snapshot.context.document_version_id))

    def _source(self,c,row,snapshot,*,require_current_runtime=True,allow_prior_runtime=False):
        if require_current_runtime and (row['configuration_sha256']!=self.store.config_hash
                or (not allow_prior_runtime and row['runtime_sha256']!=self.store.runtime_hash)):
            _error('FRAMEWORK_CONFIGURATION_CHANGED')
        document=DocumentRef.model_validate(row['input_json']['document'])
        validate_draft(snapshot.context,document,snapshot.criteria)
        step=c.execute(select(steps).where(steps.c.run_id==snapshot.context.run_id,steps.c.role=='A02')).mappings().one_or_none()
        if (step is None or step['state']!='completed' or step['output_sha256']!=digest(snapshot.criteria)
                or step['output_json']!=payload(snapshot.criteria)):_error('CRITERIA_SOURCE_INTEGRITY_INVALID',503)
        doc=c.execute(select(documents).where(documents.c.project_id==row['project_id'],
            documents.c.document_version_id==UUID(document.document_version_id))).mappings().one_or_none()
        if doc is None or str(doc['file_id'])!=document.file_id or doc['sha256']!=document.sha256:
            _error('CRITERIA_SOURCE_INTEGRITY_INVALID',503)
        latest=c.execute(select(documents.c.document_version_id).where(documents.c.project_id==row['project_id'],
            documents.c.document_id==doc['document_id']).order_by(documents.c.version_no.desc()).limit(1)).scalar_one()
        if latest!=doc['document_version_id']:_error('CRITERIA_DOCUMENT_STALE')
        return doc

    @staticmethod
    def _latest(c,run_id):
        return c.execute(select(versions).where(versions.c.source_run_id==run_id)
            .order_by(versions.c.version_no.desc()).limit(1)).mappings().one_or_none()

    @staticmethod
    def _record(row):
        try:
            version=CriteriaVersion.model_validate(row['snapshot_json'])
            if (digest(row['snapshot_json'])!=row['snapshot_sha256'] or version.criteria_version_id!=row['criteria_version_id']
                    or version.context.run_id!=row['source_run_id'] or version.source_run_id!=row['source_run_id']
                    or version.context.project_id!=str(row['project_id'])
                    or version.context.document_version_id!=str(row['document_version_id'])
                    or version.context.criteria_version_id!=str(version.criteria_version_id)):
                raise ValueError('criteria record mismatch')
            _criteria(version.context,version.criteria)
            return CriteriaVersionRecord(version=version,sha256=row['snapshot_sha256'],approval_blockers=approval_blockers(version.criteria))
        except (ValueError,TypeError):_error('CRITERIA_RECORD_INTEGRITY_INVALID',503)

    def _version(self,c,run_id,version_id):
        row=c.execute(select(versions).where(versions.c.source_run_id==run_id,
            versions.c.criteria_version_id==version_id)).mappings().one_or_none()
        if row is None:_error('RESOURCE_NOT_FOUND',404)
        return self._record(row)

    def overview(self,subject,run_id):
        with unit_of_work(self.engine) as c:
            _row,snapshot=self._open(c,subject,run_id)
            latest=self._latest(c,run_id)
            return {'source_draft_sha256':digest(snapshot.criteria),
                'latest_version':self._record(latest) if latest is not None else None,
                'approved_criteria_version_id':snapshot.approved_criteria_version_id,
                'synthetic':snapshot.synthetic,'execution_status':snapshot.execution_status}

    def get(self,subject,run_id,version_id):
        with unit_of_work(self.engine) as c:
            self._open(c,subject,run_id)
            return self._version(c,run_id,version_id)

    def history(self,subject,run_id):
        with unit_of_work(self.engine) as c:
            self._open(c,subject,run_id)
            rows=c.execute(select(versions).where(versions.c.source_run_id==run_id).order_by(versions.c.version_no)).mappings().all()
            return [self._record(r) for r in rows]

    def start_addition(self, subject, run_id, request_key, request: CriteriaAdditionRequest):
        from .model_route import route_of
        from .postgres_schema import TEXT_REVISIONS, check_extension

        admission = CriteriaAddition(source_run_id=run_id, request_key=request_key,
            parent_version_id=request.parent_version_id, expected_base_sha256=request.expected_base_sha256,
            criterion_ids=request.criterion_ids)
        try:
            raw = request.text.encode('utf-8')
        except UnicodeError:
            _error('REQUIREMENT_TEXT_INVALID', 422)
        with unit_of_work(self.engine) as c:
            row, snapshot = self._open(c, subject, run_id, write=True)
            project_id = row['project_id']
            job_key = uuid5(NAMESPACE_URL, f'creative-checker-addition:{run_id}:{request_key}')
            existing = c.execute(select(runs).where(runs.c.run_id == extraction_run_id(project_id, job_key))).mappings().one_or_none()
            if existing is not None:
                fixed = existing['input_json']
                if (existing['project_id'] != project_id or existing['actor_id'] != row['actor_id']
                        or fixed.get('criteria_addition') != payload(admission)
                        or fixed['document']['sha256'] != sha256(raw).hexdigest()):
                    _error('CRITERIA_IDEMPOTENCY_CONFLICT')
                return RunSnapshot.model_validate(existing['snapshot_json'])
            if check_extension(c)['revision'] not in TEXT_REVISIONS:
                _error('FRAMEWORK_TEXT_SCHEMA_NOT_READY', 503)
            self._addition_base(c, row, snapshot, admission)
            model_route = route_of(row['input_json'])
        self._verify_file(subject, run_id)
        uploaded = self.documents.register(subject, False, project_id, job_key, raw,
            filename='requirements.txt', media_type='text/plain', wait_for_replay=True)
        return self.store.start_pdf(subject, project_id, uploaded.document.document_version_id, job_key,
            model_route=model_route, criteria_addition=admission)

    def _addition_base(self, c, row, snapshot, admission):
        if snapshot.execution_status != 'waiting_criteria':
            _error('CRITERIA_ALREADY_APPROVED')
        doc = self._source(c, row, snapshot, allow_prior_runtime=True)
        latest = self._latest(c, admission.source_run_id)
        parent = self._record(latest).version if latest is not None else None
        base_hash = latest['snapshot_sha256'] if latest is not None else digest(snapshot.criteria)
        if admission.parent_version_id != (parent.criteria_version_id if parent else None) or admission.expected_base_sha256 != base_hash:
            _error('CRITERIA_VERSION_CONFLICT')
        current = parent.criteria if parent else snapshot.criteria.criteria
        available = {criterion.criterion_id for criterion in current if criterion.source.kind != 'direct_input'}
        if not available or (admission.criterion_ids is not None and not set(admission.criterion_ids) <= available):
            _error('CRITERIA_ADDITION_SELECTION_INVALID', 422)
        return doc, parent

    def complete_addition(self, c, job_run, extracted, now):
        """Commit the new version with the existing A02 worker result in the same transaction."""
        admission = CriteriaAddition.model_validate(job_run['input_json']['criteria_addition'])
        row = self.store._row(c, admission.source_run_id, lock=True)
        if row['project_id'] != job_run['project_id'] or row['actor_id'] != job_run['actor_id']:
            _error('CRITERIA_ADDITION_SOURCE_MISMATCH')
        if extracted.extraction is None or extracted.extraction.input_kind != 'free_text':
            _error('CRITERIA_ADDITION_SOURCE_MISMATCH')
        snapshot = RunSnapshot.model_validate(row['snapshot_json'])
        doc, parent = self._addition_base(c, row, snapshot, admission)
        request = CriteriaVersionRequest(parent_version_id=admission.parent_version_id,
            expected_base_sha256=admission.expected_base_sha256, purpose='human_review')
        version_id = self._version_id(admission.source_run_id, admission.request_key)
        if c.execute(select(versions.c.criteria_version_id).where(versions.c.criteria_version_id == version_id)).first():
            _error('CRITERIA_IDEMPOTENCY_CONFLICT')
        current = parent.criteria if parent else snapshot.criteria.criteria
        kept = (len(current) if admission.criterion_ids is None else
            sum(item.source.kind == 'direct_input' or item.criterion_id in admission.criterion_ids for item in current))
        omitted = len(current) - kept
        if kept + len(extracted.criteria) > 128 or omitted + len(extracted.criteria) > 128:
            _error('CRITERIA_ADDITION_LIMIT_EXCEEDED', 422)
        if extracted.extraction.rejected_candidates or any('model_output_incomplete' in warning for warning in extracted.warnings):
            _error('CRITERIA_ADDITION_INCOMPLETE', 422)
        version = revise_criteria(snapshot.criteria, parent, request, version_id=version_id,
            created_by=row['actor_id'], created_at=now, addition=(admission, extracted))
        audit = payload(admission) | {'analysis_run_id': extracted.context.run_id,
            'analysis_document_sha256': extracted.extraction.document_sha256, 'analysis_draft_sha256': digest(extracted)}
        return self._insert_version(c, row, doc, version, admission.request_key, audit)

    @staticmethod
    def _version_id(run_id, request_key):
        return uuid5(NAMESPACE_URL, f'creative-checker-criteria:{run_id}:{request_key}')

    def _insert_version(self, c, row, doc, version, request_key, request_json):
        run_id = version.source_run_id
        value = payload(version)
        value_hash = digest(value)
        fingerprint = digest({'actor_id': str(row['actor_id']), 'request': request_json})
        inserted = c.execute(insert(versions).values(criteria_version_id=version.criteria_version_id,source_run_id=run_id,
            project_id=row['project_id'],document_id=doc['document_id'],document_version_id=doc['document_version_id'],
            parent_version_id=version.parent_version_id,version_no=version.version_no,request_key=request_key,
            request_json=request_json,request_sha256=fingerprint,snapshot_json=value,snapshot_sha256=value_hash,
            created_by=row['actor_id'],created_at=version.created_at).returning(versions)).mappings().one()
        self.store.event(c,run_id,'criteria.version_created',criteria_version_id=str(version.criteria_version_id),
            sha256=value_hash,synthetic=version.synthetic)
        return self._record(inserted)

    def create(self,subject,run_id,request_key,request):
        request=CriteriaVersionRequest.model_validate(request.model_dump(mode='python'))
        self.preflight(subject,run_id);self._verify_file(subject,run_id)
        fingerprint=digest({'actor_id':str(subject.user_id),'request':payload(request)})
        with unit_of_work(self.engine) as c:
            row,snapshot=self._open(c,subject,run_id,write=True)
            existing=c.execute(select(versions).where(versions.c.source_run_id==run_id,versions.c.request_key==request_key)).mappings().one_or_none()
            if existing is not None:
                if existing['request_sha256']!=fingerprint:_error('CRITERIA_IDEMPOTENCY_CONFLICT')
                return self._record(existing)
            if snapshot.execution_status!='waiting_criteria':_error('CRITERIA_ALREADY_APPROVED')
            latest=self._latest(c,run_id)
            parent=self._record(latest).version if latest is not None else None
            doc=self._source(c,row,snapshot,allow_prior_runtime=bool(parent and parent.addition_run_ids))
            base_hash=latest['snapshot_sha256'] if latest is not None else digest(snapshot.criteria)
            if request.parent_version_id!=(parent.criteria_version_id if parent else None) or request.expected_base_sha256!=base_hash:
                _error('CRITERIA_VERSION_CONFLICT')
            now=c.execute(select(func.clock_timestamp())).scalar_one()
            version_id=self._version_id(run_id,request_key)
            try:version=revise_criteria(snapshot.criteria,parent,request,version_id=version_id,created_by=subject.user_id,created_at=now)
            except ValueError:_error('CRITERIA_EDIT_INVALID',422)
            return self._insert_version(c,row,doc,version,request_key,payload(request))

    def _approved(self,c,run_id):
        binding=c.execute(select(bindings).where(bindings.c.source_run_id==run_id)).mappings().one_or_none()
        if binding is None:_error('RESOURCE_NOT_FOUND',404)
        approval=c.execute(select(approvals).where(approvals.c.approval_id==binding['approval_id'])).mappings().one()
        version=self._version(c,run_id,binding['criteria_version_id'])
        evidence=EvidenceBundle.model_validate(binding['evidence_json'])
        approved=Approval.model_validate(approval['payload_json'])
        if (version.sha256!=binding['criteria_sha256'] or digest(approval['payload_json'])!=approval['payload_sha256']
                or approved.approved_criteria_version_id!=str(version.version.criteria_version_id)
                or approved.criteria_sha256!=version.sha256):
            _error('CRITERIA_RECORD_INTEGRITY_INVALID',503)
        validate_evidence(version.version.context,version.version.criteria,evidence)
        return CriteriaApprovalRecord(approval_id=approval['approval_id'],approval=approved,
            approved_at=approval['approved_at'],criteria=version,evidence=evidence,purpose=binding['request_json']['purpose'],synthetic=binding['synthetic'])

    def approved(self,subject,run_id):
        with unit_of_work(self.engine) as c:
            _row,snapshot=self._open(c,subject,run_id)
            result=self._approved(c,run_id)
            if snapshot.approved_criteria_version_id!=str(result.criteria.version.criteria_version_id):
                _error('CRITERIA_RECORD_INTEGRITY_INVALID',503)
            return result

    def approve(self,subject,run_id,request_key,request):
        request=CriteriaApprovalRequest.model_validate(request.model_dump(mode='python'))
        self.preflight(subject,run_id);self._verify_file(subject,run_id)
        fingerprint=digest({'actor_id':str(subject.user_id),'request':payload(request)})
        with unit_of_work(self.engine) as c:
            row,snapshot=self._open(c,subject,run_id,write=True)
            existing=c.execute(select(bindings).where(bindings.c.source_run_id==run_id)).mappings().one_or_none()
            if existing is not None:
                if existing['request_key']!=request_key or existing['request_sha256']!=fingerprint:_error('CRITERIA_APPROVAL_CONFLICT')
                return self._approved(c,run_id)
            if snapshot.execution_status!='waiting_criteria':_error('CRITERIA_ALREADY_APPROVED')
            latest=self._latest(c,run_id)
            if (latest is None or latest['criteria_version_id']!=request.criteria_version_id
                    or latest['snapshot_sha256']!=request.expected_sha256):_error('CRITERIA_VERSION_CONFLICT')
            version=self._record(latest)
            self._source(c,row,snapshot,allow_prior_runtime=bool(version.version.addition_run_ids))
            if version.approval_blockers:_error('CRITERIA_UNRESOLVED')
            synthetic=snapshot.synthetic or version.version.synthetic or request.purpose=='synthetic_test'
            evidence=freeze_approved_context(version.version.context,version.version.criteria,synthetic=synthetic)
            approval=Approval(context=snapshot.context,actor_id=str(subject.user_id),decision='approve_criteria',
                reason='Criteria '+str(request.criteria_version_id)+'@'+request.expected_sha256+': '+request.reason,
                criterion_ids=tuple(c.criterion_id for c in version.version.criteria),
                approved_criteria_version_id=str(request.criteria_version_id),criteria_sha256=request.expected_sha256)
            approval_id=uuid5(NAMESPACE_URL,'creative-checker-criteria-approval:'+run_id+':'+str(request_key))
            c.execute(insert(approvals).values(approval_id=approval_id,run_id=run_id,project_id=row['project_id'],actor_id=subject.user_id,
                decision='approve_criteria',payload_json=payload(approval),payload_sha256=digest(approval)))
            c.execute(insert(bindings).values(approval_id=approval_id,source_run_id=run_id,project_id=row['project_id'],
                criteria_version_id=request.criteria_version_id,criteria_sha256=request.expected_sha256,request_key=request_key,
                request_json=payload(request),request_sha256=fingerprint,evidence_json=payload(evidence),synthetic=synthetic))
            self.store._save(c,snapshot.model_copy(update={'execution_status':'waiting_asset','synthetic':synthetic,
                'approvals':snapshot.approvals+(approval,),'approved_criteria_version_id':str(request.criteria_version_id)}))
            self.store.event(c,run_id,'criteria.approved',approval_id=str(approval_id),criteria_version_id=str(request.criteria_version_id),
                sha256=request.expected_sha256,synthetic=synthetic)
            # Intentionally no review job: a real poster/version is still absent.
            return self._approved(c,run_id)
