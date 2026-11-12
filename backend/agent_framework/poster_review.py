"""Bind approved criteria to existing M008 poster files, never caller observations.

The first poster and each revision require a fresh explicit criteria confirmation.
No source approval or final decision is copied to a new poster run.
"""
import importlib
import inspect
from uuid import UUID,uuid5,NAMESPACE_URL
from datetime import datetime,timezone

from sqlalchemy import select

from backend.app.persistence.transaction import unit_of_work
from backend.app.security.errors import AuthError
from backend.app.services.business import table
from backend.app.storage.local import LocalStorage,StorageError,key
from .contracts import (Contract,Hash,AssetRef,Context,DocumentRef,ApprovedBaseline,CriteriaDraft,
    RunSnapshot,StepRecord,ReportDraft)
from .criteria_review import PostgresCriteriaReview
from .adapters.project_files import LegacyStorageReader
from .files import StoredFileMetadata,VerifiedFileAccess
from .context import freeze_approved_context
from .ports import AdapterFailure
from .postgres import runs,coordinator,payload,digest


class PosterReviewRequest(Contract):
    criteria_version_id: UUID
    expected_criteria_sha256: Hash
    asset_version_id: UUID


def open_poster_storage(root):
    storage=LocalStorage(root)
    try:
        try:
            with storage.read('framework-pdf-marker'):raise StorageError('POSTER_ROOT_MUST_NOT_BE_PDF')
        except FileNotFoundError:pass
        storage.start(shared=True)
        return storage
    except BaseException:
        storage.close();raise


def poster_metadata(c,pid,version_id):
    images=table('asset_versions');files=table('stored_files')
    image=c.execute(select(images).where(images.c.project_id==pid,images.c.asset_version_id==version_id)).mappings().one_or_none()
    if image is None:raise AuthError('RESOURCE_NOT_FOUND',404)
    file=c.execute(select(files).where(files.c.project_id==pid,files.c.file_id==image['display_file_id'])).mappings().one_or_none()
    if (file is None or file['state']!='stored' or file['storage_backend']!='local' or file['file_role']!='display_image'
            or file['parent_file_id']!=image['original_file_id'] or file['detected_mime']!='image/png'
            or file['storage_key']!=key(pid,file['file_id'])):
        raise AuthError('POSTER_FILE_UNAVAILABLE',503)
    reference=AssetRef(file_id=str(file['file_id']),asset_version_id=str(version_id),sha256=file['sha256'],
        media_type='image/png',width=image['display_width'],height=image['display_height'])
    metadata=StoredFileMetadata(file_id=reference.file_id,project_id=str(pid),version_id=str(version_id),kind='poster',
        storage_key=file['storage_key'],media_type=reference.media_type,byte_size=file['byte_size'],sha256=reference.sha256,
        width=reference.width,height=reference.height,coordinate_space='display')
    return reference,metadata


class _PosterCatalog:
    def __init__(self,store,subject=None):self.store,self.subject=store,subject
    def lookup_asset(self,context,reference):
        if context.asset_version_id!=reference.asset_version_id:raise AdapterFailure('FILE_REFERENCE_MISMATCH','Poster version differs.')
        with unit_of_work(self.store.engine) as c:
            if self.subject is None:self.store.worker_access(c,UUID(context.project_id),UUID(context.actor_id))
            else:self.store._access(c,self.subject,context,write=False)
            expected,metadata=poster_metadata(c,UUID(context.project_id),UUID(context.asset_version_id))
            if expected!=reference:raise AdapterFailure('FILE_REFERENCE_MISMATCH','Poster metadata differs.')
            return metadata


def read_registered_poster(store,storage,context,reference,subject=None):
    return VerifiedFileAccess(_PosterCatalog(store,subject),LegacyStorageReader(storage)).read_asset(context,reference)


def validate_pinned_review(store,c,row):
    fixed=row['input_json'];raw=fixed.get('baseline')
    if raw is None:return
    baseline=ApprovedBaseline.model_validate(raw);snapshot=RunSnapshot.model_validate(row['snapshot_json'])
    if (snapshot.baseline!=baseline or snapshot.criteria is None or fixed['context']!=payload(snapshot.context)):
        raise AuthError('POSTER_BASELINE_MISMATCH',409)
    source=store._row(c,baseline.source_run_id);source_snapshot=RunSnapshot.model_validate(source['snapshot_json'])
    if source['project_id']!=row['project_id'] or source['actor_id']!=row['actor_id']:
        raise AuthError('POSTER_BASELINE_MISMATCH',409)
    service=PostgresCriteriaReview(store)
    approved=service._approved(c,baseline.source_run_id)
    version=approved.criteria.version
    service._source(c,source,source_snapshot,require_current_runtime=False)
    doc=DocumentRef.model_validate(fixed['document']);asset=AssetRef.model_validate(fixed['asset'])
    if (str(version.criteria_version_id)!=baseline.criteria_version_id or approved.criteria.sha256!=baseline.criteria_sha256
            or str(approved.approval_id)!=baseline.approval_id or version.document_sha256!=baseline.document_sha256
            or source_snapshot.approved_criteria_version_id!=baseline.criteria_version_id
            or snapshot.context.criteria_version_id!=baseline.criteria_version_id
            or snapshot.context.document_version_id!=version.context.document_version_id
            or doc.document_version_id!=version.context.document_version_id or doc.sha256!=baseline.document_sha256
            or snapshot.criteria.context!=snapshot.context or snapshot.criteria.criteria!=version.criteria
            or snapshot.context.asset_version_id!=asset.asset_version_id
            or (approved.synthetic and not snapshot.synthetic)):
        raise AuthError('POSTER_BASELINE_MISMATCH',409)
    expected,_=poster_metadata(c,row['project_id'],UUID(asset.asset_version_id))
    if expected!=asset:raise AuthError('POSTER_ASSET_MISMATCH',409)
    if snapshot.evidence is not None:
        expected_evidence=freeze_approved_context(snapshot.context,version.criteria,synthetic=snapshot.evidence.synthetic)
        if snapshot.evidence.context_id.startswith('rag-'):
            from .evidence_context import check_stored
            check_stored(c,row,snapshot,expected_evidence)
            return
        if snapshot.evidence!=expected_evidence or (approved.synthetic and not snapshot.evidence.synthetic):
            raise AuthError('POSTER_BASELINE_MISMATCH',409)


class PostgresPosterReviews:
    def __init__(self,store,criteria_review,poster_storage):
        self.store,self.criteria,self.storage=store,criteria_review,poster_storage

    def preflight(self,subject,run_id):
        with unit_of_work(self.store.engine) as c:
            row=self.store._row(c,run_id);snapshot=RunSnapshot.model_validate(row['snapshot_json'])
            self.store._access(c,subject,snapshot.context)

    @staticmethod
    def _pin(approved):
        return ApprovedBaseline(source_run_id=approved.criteria.version.source_run_id,
            criteria_version_id=str(approved.criteria.version.criteria_version_id),criteria_sha256=approved.criteria.sha256,
            approval_id=str(approved.approval_id),document_sha256=approved.criteria.version.document_sha256)

    @staticmethod
    def _check_request(approved,request):
        if (str(request.criteria_version_id)!=str(approved.criteria.version.criteria_version_id)
                or request.expected_criteria_sha256!=approved.criteria.sha256):raise AuthError('CRITERIA_VERSION_CONFLICT',409)

    def _verify_files(self,subject,source_id,request):
        self.criteria._verify_file(subject,source_id)
        approved=self.criteria.approved(subject,source_id);self._check_request(approved,request)
        with unit_of_work(self.store.engine) as c:
            context=approved.criteria.version.context
            self.store._access(c,subject,context)
            reference,_=poster_metadata(c,UUID(context.project_id),request.asset_version_id)
        context=context.model_copy(update={'asset_version_id':reference.asset_version_id})
        read_registered_poster(self.store,self.storage,context,reference,subject)

    def create(self,subject,source_run_id,request_key,request,*,parent_run_id=None):
        request=PosterReviewRequest.model_validate(request.model_dump(mode='python'))
        self.preflight(subject,parent_run_id or source_run_id)
        self._verify_files(subject,source_run_id,request)
        with unit_of_work(self.store.engine) as c:
            source,source_snapshot=self.criteria._open(c,subject,source_run_id,write=True)
            approved=self.criteria._approved(c,source_run_id);self._check_request(approved,request)
            self.criteria._source(c,source,source_snapshot,require_current_runtime=False)
            baseline=self._pin(approved);version=approved.criteria.version
            asset,_=poster_metadata(c,source['project_id'],request.asset_version_id)
            document=DocumentRef.model_validate(source['input_json']['document'])
            run_id=str(uuid5(NAMESPACE_URL,'creative-checker-poster:'+(parent_run_id or source_run_id)+':'+str(request_key)))
            context=Context(project_id=str(source['project_id']),actor_id=str(subject.user_id),run_id=run_id,
                document_version_id=document.document_version_id,criteria_version_id=baseline.criteria_version_id,
                asset_version_id=asset.asset_version_id,parent_run_id=parent_run_id)
            fixed={'context':payload(context),'document':payload(document),'asset':payload(asset),'baseline':payload(baseline)}
            # 포스터 검사(A04/A05)는 기준을 만든 A02 실행의 모델 경로(보안 여부)를 그대로 물려받는다(수정본 재검수 포함).
            from .model_route import route_of,stored
            model_route=stored(route_of(source['input_json']))
            if model_route is not None:fixed['model_route']=model_route
            old=c.execute(select(runs).where(runs.c.run_id==run_id)).mappings().one_or_none()
            if old is not None:
                if (old['input_sha256']!=digest(fixed) or old['configuration_sha256']!=self.store.config_hash
                        or old['runtime_sha256']!=self.store.runtime_hash):raise AuthError('FRAMEWORK_IDEMPOTENCY_CONFLICT',409)
                validate_pinned_review(self.store,c,old)
                return RunSnapshot.model_validate(old['snapshot_json'])
            if parent_run_id:
                parent=self.store._row(c,parent_run_id,lock=True)
                self.store._access(c,subject,RunSnapshot.model_validate(parent['snapshot_json']).context)
                validate_pinned_review(self.store,c,parent)
                if parent['input_json'].get('baseline')!=payload(baseline):raise AuthError('POSTER_BASELINE_MISMATCH',409)
                if c.execute(select(runs.c.run_id).where(runs.c.parent_run_id==parent_run_id)).first():
                    raise AuthError('FRAMEWORK_REVISION_CONFLICT',409)
                parent_snapshot=RunSnapshot.model_validate(parent['snapshot_json'])
                if parent_snapshot.execution_status!='waiting_revision' or parent_snapshot.human_decision!='changes_approved':
                    raise AuthError('POSTER_CHANGE_APPROVAL_REQUIRED',409)
                if context.asset_version_id==parent_snapshot.context.asset_version_id:
                    raise AuthError('POSTER_NEW_ASSET_REQUIRED',409)
                flow=self.store._restore(parent)
                snapshot=flow.rereview(parent_run_id,context,asset)
            else:
                draft=CriteriaDraft(context=context,criteria=version.criteria,synthetic=approved.synthetic,
                    warnings=('Approved version reused; a fresh poster-input confirmation is required.',))
                snapshot=RunSnapshot(context=context,profile=self.store.configuration.profile,synthetic=approved.synthetic,
                    execution_status='waiting_criteria',criteria=draft,baseline=baseline,stages=('A01','A02_approved_version_reused'),
                    step_records=(StepRecord(ordinal=1,role='A02',status='reused',recorded_at=datetime.now(timezone.utc),
                        source_run_id=source_run_id,synthetic=approved.synthetic),))
            self.store._create(c,snapshot,document,asset,model_route=model_route)
            self.store.event(c,source_run_id,'poster.review_created',poster_run_id=run_id,criteria_sha256=baseline.criteria_sha256)
            return snapshot

    def revise(self,subject,parent_id,request_key,request):
        with unit_of_work(self.store.engine) as c:
            parent=self.store._row(c,parent_id);snapshot=RunSnapshot.model_validate(parent['snapshot_json'])
            self.store._access(c,subject,snapshot.context)
            if snapshot.baseline is None:raise AuthError('POSTER_BASELINE_REQUIRED',409)
        return self.create(subject,snapshot.baseline.source_run_id,request_key,request,parent_run_id=parent_id)


def build_poster_callback(reference):
    try:
        from .composition import _asynchronous
        module,name=reference.split(':',1);factory=getattr(importlib.import_module(module),name)
        if not callable(factory) or _asynchronous(factory):raise ValueError()
        inspect.signature(factory).bind();callback=factory();method=getattr(callback,'inspect_poster',None)
        if inspect.isclass(callback) or not callable(method) or _asynchronous(method):raise ValueError()
        inspect.signature(method).bind(object(),object())
        return callback
    except Exception:raise AdapterFailure('POSTER_FACTORY_INVALID','The trusted factory must implement synchronous inspect_poster(request, poster).') from None


class RegisteredPosterInspector:
    def __init__(self,store,poster_storage,read_pdf,callback):
        self.store,self.storage,self.read_pdf,self.callback=store,poster_storage,read_pdf,callback
    def inspect(self,request):
        from .contracts import ExtractRequest
        with unit_of_work(self.store.engine) as c:
            row=self.store._row(c,request.context.run_id)
            if row['input_json'].get('baseline') is None:
                raise AdapterFailure('POSTER_BASELINE_REQUIRED','Registered poster inspection requires an approved version binding.')
            validate_pinned_review(self.store,c,row)
            document=DocumentRef.model_validate(row['input_json']['document'])
        pdf_request=ExtractRequest(context=request.context,document=document,synthetic=request.synthetic)
        self.read_pdf(pdf_request)
        poster=read_registered_poster(self.store,self.storage,request.context,request.asset)
        # 일반 자료(openai 경로)는 경로를 아는 어댑터만 처리한다. 모르는 어댑터가 Qwen으로 보내 경로가 바뀌지 않게 막는다.
        from .model_route import current_route
        if current_route()=='openai' and not getattr(self.callback,'openai_route',False):
            raise AdapterFailure('OPENAI_ROUTE_UNSUPPORTED','설정된 포스터 검사 어댑터가 OpenAI 경로(일반 자료)를 지원하지 않습니다.')
        result=self.callback.inspect_poster(request,poster)
        # A provider can be slow. Recheck stored bytes after it returns, outside
        # transactions, so a changed file cannot produce a successful stage.
        self.read_pdf(pdf_request)
        read_registered_poster(self.store,self.storage,request.context,request.asset)
        return result


class GroundedReporter:
    def report(self,request):
        items=request.validated.items
        return ReportDraft(context=request.context,verdict=request.validated.verdict,
            summary='검증된 기계 판정: '+request.validated.verdict+'. 조건 '+str(len(items))+'개. 실제 사람의 최종 승인은 별도입니다.',
            evidence_ids=tuple(e.evidence_id for e in request.evidence.evidence),
            change_requests=tuple(item.criterion_id+': '+', '.join(item.issues) for item in items if item.verdict!='pass'),
            synthetic=request.synthetic or request.validated.synthetic or request.evidence.synthetic)


def make_reporter():return GroundedReporter()
