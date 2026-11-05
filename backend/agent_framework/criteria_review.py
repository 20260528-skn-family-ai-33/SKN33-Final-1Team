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
from .criteria_contracts import (CriteriaApprovalRecord,CriteriaApprovalRequest,CriteriaVersion,
    CriteriaVersionRecord,CriteriaVersionRequest,RecordedCriteriaEdit)
from .context import freeze_approved_context
from .documents import versions as documents
from .postgres import approvals,runs,steps,payload,digest
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


def revise_criteria(draft,parent,request,*,version_id,created_by,created_at):
    """Pure revision logic. Preserves the original draft and PDF anchors across versions."""
    originals={c.criterion_id:c for c in draft.criteria}
    current={c.criterion_id:c for c in (parent.criteria if parent else draft.criteria)}
    excluded={c.criterion_id:c for c in parent.exclusions} if parent else {}
    synthetic=draft.synthetic or bool(parent and parent.synthetic) or request.purpose=='synthetic_test'
    changes=[]
    for edit in request.edits:
        if edit.criterion_id not in originals:raise ValueError('unknown criterion')
        source=CriteriaValueSource(record_id=str(uuid5(NAMESPACE_URL,str(version_id)+':'+edit.criterion_id)),
            purpose=request.purpose,reference=edit.source.reference,quote=edit.source.quote,
            quote_sha256=sha256(edit.source.quote.encode()).hexdigest(),submitted_by=str(created_by),
            submitted_at=created_at,synthetic=synthetic)
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
    if not current:raise ValueError('cannot exclude all criteria')
    criteria=tuple(current[cid] for cid in originals if cid in current)
    context=draft.context.model_copy(update={'criteria_version_id':str(version_id)})
    _criteria(context,criteria)
    return CriteriaVersion(criteria_version_id=version_id,source_run_id=context.run_id,context=context,
        parent_version_id=parent.criteria_version_id if parent else None,version_no=parent.version_no+1 if parent else 1,
        source_draft_sha256=digest(draft),document_sha256=draft.extraction.document_sha256,
        criteria=criteria,exclusions=tuple(excluded.values()),changes=tuple(changes),created_by=str(created_by),
        created_at=created_at,purpose=request.purpose,synthetic=synthetic)


class PostgresCriteriaReview:
    def __init__(self,store,document_service=None):
        self.store,self.engine,self.documents=store,store.engine,document_service

    def _open(self,c,subject,run_id,*,write=False):
        row=self.store._row(c,run_id)
        snapshot=RunSnapshot.model_validate(row['snapshot_json'])
        self.store._access(c,subject,snapshot.context,write=write)
        if write:row=self.store._row(c,run_id,lock=True);snapshot=RunSnapshot.model_validate(row['snapshot_json'])
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

    def _source(self,c,row,snapshot,*,require_current_runtime=True):
        if require_current_runtime and (row['configuration_sha256']!=self.store.config_hash or row['runtime_sha256']!=self.store.runtime_hash):
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
            doc=self._source(c,row,snapshot)
            latest=self._latest(c,run_id)
            parent=self._record(latest).version if latest is not None else None
            base_hash=latest['snapshot_sha256'] if latest is not None else digest(snapshot.criteria)
            if request.parent_version_id!=(parent.criteria_version_id if parent else None) or request.expected_base_sha256!=base_hash:
                _error('CRITERIA_VERSION_CONFLICT')
            now=c.execute(select(func.clock_timestamp())).scalar_one()
            version_id=uuid5(NAMESPACE_URL,'creative-checker-criteria:'+run_id+':'+str(request_key))
            try:version=revise_criteria(snapshot.criteria,parent,request,version_id=version_id,created_by=subject.user_id,created_at=now)
            except ValueError:_error('CRITERIA_EDIT_INVALID',422)
            value=payload(version);value_hash=digest(value)
            inserted=c.execute(insert(versions).values(criteria_version_id=version_id,source_run_id=run_id,project_id=row['project_id'],
                document_id=doc['document_id'],document_version_id=doc['document_version_id'],parent_version_id=request.parent_version_id,
                version_no=version.version_no,request_key=request_key,request_json=payload(request),request_sha256=fingerprint,
                snapshot_json=value,snapshot_sha256=value_hash,created_by=subject.user_id,created_at=now).returning(versions)).mappings().one()
            self.store.event(c,run_id,'criteria.version_created',criteria_version_id=str(version_id),sha256=value_hash,synthetic=version.synthetic)
            return self._record(inserted)

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
            self._source(c,row,snapshot)
            latest=self._latest(c,run_id)
            if (latest is None or latest['criteria_version_id']!=request.criteria_version_id
                    or latest['snapshot_sha256']!=request.expected_sha256):_error('CRITERIA_VERSION_CONFLICT')
            version=self._record(latest)
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
