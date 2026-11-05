"""Fresh framework entrypoint reusing M008 authentication, CSRF and app Engine.

No OCR/background-task leader, frontend build, model initialization or migration.
POST admission/approval only queues work; the separate worker invokes adapters.
"""
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import partial
from urllib.parse import quote
from uuid import UUID

from fastapi import Header, Request, Response
from starlette.concurrency import run_in_threadpool

from backend.app.main import create_app as create_auth_app
from backend.app.persistence.transaction import unit_of_work
from backend.app.persistence.database import PersistenceError
from backend.app.security.csrf import developer_allowed
from backend.app.security.errors import AuthError
from backend.app.config import ConfigurationError
from backend.app.services.business import Business
from backend.app.routes.v1.business import project_router, image_router
from backend.app.services.files import Files
from .api import StartRequest,RevisionRequest
from .contracts import Approval,RunSnapshot,StepTimeline
from .postgres import PostgresStore
from .postgres_schema import check_extension,check_app_permissions,check_document_extension
from .documents import PostgresDocuments,RegisteredDocument,TextDocumentInput,open_document_storage
from .files import FileLimits
from .criteria_review import PostgresCriteriaReview
from .criteria_contracts import CriteriaVersionRequest,CriteriaVersionRecord,CriteriaApprovalRequest,CriteriaApprovalRecord
from .poster_review import PosterReviewRequest,PostgresPosterReviews,open_poster_storage


def create_postgres_app(settings,configuration,*,engine=None,pdf_storage_root=None,poster_storage_root=None):
    if settings.environment not in ('development','test','production'):
        raise ConfigurationError('Unsupported framework environment.')
    if settings.environment=='production':
        from .deployment import require_verified_database,require_live_configuration
        require_verified_database(settings)
        require_live_configuration(configuration)
    # The existing authentication app deliberately skips all legacy media and
    # review executors when storage/frontend are unset.
    settings=replace(settings,storage_root=None,frontend_dist=None,review_pipeline='disabled',
                     review_executor='background_tasks',review_config_file=None)
    app=create_auth_app(settings=settings,engine=engine)
    original=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(current):
        async with original(current):
            def prepare():
                with unit_of_work(current.state.engine) as c:
                    revision=check_extension(c);check_app_permissions(c)
                    if pdf_storage_root is not None:check_document_extension(c)
                if configuration.context_preparation is not None and revision['revision']!='AF005':
                    raise ConfigurationError('Neo4j evidence preparation requires explicit AF005 migration.')
                current.state.framework_revision=revision['revision']
                current.state.business=Business(current.state.engine,current.state.authorization,settings)
                return PostgresStore(current.state.engine,configuration,current.state.authorization)
            current.state.framework=await run_in_threadpool(prepare)
            storage=None
            poster_storage=None
            current.state.documents=None
            current.state.criteria_review=None
            current.state.poster_reviews=None
            try:
                if pdf_storage_root is not None:
                    storage=await run_in_threadpool(open_document_storage,pdf_storage_root)
                    current.state.documents=PostgresDocuments(current.state.engine,current.state.authorization,storage)
                if current.state.framework_revision in ('AF003','AF004','AF005'):
                    current.state.criteria_review=PostgresCriteriaReview(current.state.framework,current.state.documents)
                if poster_storage_root is not None:
                    if current.state.criteria_review is None or storage is None:
                        raise ConfigurationError('Poster reviews require AF003 and registered PDF storage.')
                    poster_storage=await run_in_threadpool(open_poster_storage,poster_storage_root)
                    if configuration.roles['A04_A05'].kind=='registered_poster':
                        current.state.files = Files(current.state.engine, poster_storage, current.state.business)
                        await run_in_threadpool(current.state.files.recover, concurrent=True)
                        current.state.poster_reviews=PostgresPosterReviews(current.state.framework,current.state.criteria_review,poster_storage)
                yield
            finally:
                if poster_storage is not None:await run_in_threadpool(poster_storage.close)
                if storage is not None:await run_in_threadpool(storage.close)
    app.router.lifespan_context=lifespan
    app.title='Creative Checker — PostgreSQL agent framework'
    app.include_router(project_router(),prefix='/api/v1')

    def subject(request):
        user=app.state.auth.current(request.cookies.get(settings.session_cookie),
            developer_allowed=developer_allowed(request,settings))
        if user is None:raise AuthError('AUTHENTICATION_REQUIRED',401)
        return user

    def invoke(action,*args):
        try:return action(*args)
        except ValueError:raise AuthError('FRAMEWORK_CONFLICT',409) from None

    prefix='/api/framework/v1'
    app.include_router(project_router(),prefix=prefix)
    if poster_storage_root is not None and configuration.roles['A04_A05'].kind == 'registered_poster':
        app.include_router(image_router(), prefix=prefix)
    from backend.app.routes.v1.auth import csrf_response
    app.add_api_route(prefix+'/csrf',csrf_response,methods=['GET'])
    @app.get(prefix+'/health')
    def health():
        return {'storage':'postgres','queue':'agent_framework.jobs','schema':app.state.framework_revision,
                'pdf_registration':app.state.documents is not None,
                'free_text_registration':app.state.documents is not None and app.state.framework_revision in ('AF004','AF005'),
                'pdf_extraction_mode':configuration.pdf_extraction.mode if configuration.pdf_extraction and app.state.documents else None,
                'requirements_presentation':bool(configuration.pdf_extraction and configuration.pdf_extraction.presentation),
                'context_preparation':configuration.context_preparation.mode if configuration.context_preparation else 'approved_sources',
                'graph_projection':app.state.framework_revision=='AF005',
                'criteria_review':app.state.criteria_review is not None,
                'poster_reviews':app.state.poster_reviews is not None,
                'authentication':'existing_M008_session_and_CSRF','adapters_initialized_in_api':False,'production_ready':False}
    @app.get('/api/health')
    def frontend_health():
        # front-v2 probes this legacy-compatible path. Readiness is API-only;
        # no inference request or claim of model connectivity is made here.
        return {'status':'ok','ready':app.state.ready,'runtime':{'profile':'agent_framework'},
                'model_connection_verified':False,**health()}
    @app.get(prefix+'/projects/{project_id}/graph-status')
    def graph_status_view(request:Request,project_id:UUID):
        from .graph_projection import graph_status,installed
        actor=subject(request)
        with unit_of_work(app.state.engine) as c:
            app.state.authorization.project(c,actor,project_id,action='read')
            if not installed(c):raise AuthError('FRAMEWORK_GRAPH_SCHEMA_NOT_READY',503)
            return graph_status(c,project_id)

    @app.get(prefix+'/projects/{project_id}/criteria-impact')
    def impact_view(request:Request,project_id:UUID,criteria_version_id:UUID,criterion_id:str):
        from .graph_projection import criteria_impact
        actor=subject(request)
        with unit_of_work(app.state.engine) as c:
            app.state.authorization.project(c,actor,project_id,action='read')
            return criteria_impact(c,project_id,criteria_version_id,criterion_id)

    @app.get(prefix+'/runs/{run_id}/evidence-context')
    def evidence_view(request:Request,run_id:str):
        from .evidence_context import read_context
        return read_context(app.state.framework,subject(request),run_id)

    @app.post(prefix+'/runs',response_model=RunSnapshot,status_code=202)
    def start(request:Request,body:StartRequest):
        return invoke(app.state.framework.start,subject(request),body.context,body.document,body.asset)
    @app.get(prefix+'/runs/{run_id}',response_model=RunSnapshot)
    def read(request:Request,run_id:str):return invoke(app.state.framework.get,subject(request),run_id)
    @app.get(prefix+'/runs/{run_id}/requirements')
    def requirements(request:Request,run_id:str):
        from .requirements_presentation import present_requirements
        snapshot=invoke(app.state.framework.get,subject(request),run_id)
        return present_requirements(snapshot)
    @app.get(prefix+'/runs/{run_id}/steps',response_model=StepTimeline)
    def timeline(request:Request,run_id:str):return invoke(app.state.framework.get_steps,subject(request),run_id)
    @app.get(prefix+'/runs/{run_id}/approvals')
    def history(request:Request,run_id:str):return invoke(app.state.framework.approval_history,subject(request),run_id)
    @app.post(prefix+'/runs/{run_id}/approvals',response_model=RunSnapshot)
    def approve(request:Request,run_id:str,body:Approval):
        return invoke(app.state.framework.approve,subject(request),run_id,body)
    @app.post(prefix+'/runs/{run_id}/revisions',response_model=RunSnapshot,status_code=201)
    def revise(request:Request,run_id:str,body:RevisionRequest):
        return invoke(app.state.framework.rereview,subject(request),run_id,body.context,body.asset)

    def documents():
        if app.state.documents is None:raise AuthError('PDF_REGISTRATION_DISABLED',503)
        return app.state.documents

    def criteria_review():
        if app.state.criteria_review is None:raise AuthError('CRITERIA_REVIEW_SCHEMA_REQUIRED',503)
        return app.state.criteria_review

    async def poster_command(request,run_id,key,revision):
        from backend.app.routes.v1.business import parse
        service=app.state.poster_reviews
        if service is None:raise AuthError('POSTER_REVIEW_DISABLED',503)
        actor=await run_in_threadpool(subject,request)
        try:
            await run_in_threadpool(service.preflight,actor,run_id)
            body=await parse(request,PosterReviewRequest)
            action=service.revise if revision else service.create
            return await run_in_threadpool(action,actor,run_id,key,body)
        except PersistenceError:raise AuthError('POSTER_ADMISSION_UNCONFIRMED',503) from None
        except ValueError:raise AuthError('POSTER_INPUT_INVALID',422) from None

    @app.post(prefix+'/runs/{run_id}/poster-reviews',response_model=RunSnapshot,status_code=201)
    async def poster_review(request:Request,run_id:str,idempotency_key:UUID=Header(alias='Idempotency-Key')):
        return await poster_command(request,run_id,idempotency_key,False)

    @app.post(prefix+'/runs/{run_id}/poster-revisions',response_model=RunSnapshot,status_code=201)
    async def poster_revision(request:Request,run_id:str,idempotency_key:UUID=Header(alias='Idempotency-Key')):
        return await poster_command(request,run_id,idempotency_key,True)

    async def review_command(request,run_id,model,action,request_key):
        from backend.app.routes.v1.business import parse
        service=criteria_review()
        actor=await run_in_threadpool(subject,request)
        try:
            await run_in_threadpool(service.preflight,actor,run_id)
            body=await parse(request,model)  # Existing bounded/strict JSON parser.
            return await run_in_threadpool(action,actor,run_id,request_key,body)
        except PersistenceError:raise AuthError('CRITERIA_WRITE_UNCONFIRMED',503) from None
        except ValueError:raise AuthError('CRITERIA_REQUEST_INVALID',422) from None

    @app.get(prefix+'/runs/{run_id}/criteria-review')
    def review_overview(request:Request,run_id:str):
        return criteria_review().overview(subject(request),run_id)

    @app.post(prefix+'/runs/{run_id}/criteria-versions',response_model=CriteriaVersionRecord,status_code=201)
    async def revise_criteria(request:Request,run_id:str,idempotency_key:UUID=Header(alias='Idempotency-Key')):
        return await review_command(request,run_id,CriteriaVersionRequest,criteria_review().create,idempotency_key)

    @app.get(prefix+'/runs/{run_id}/criteria-versions',response_model=list[CriteriaVersionRecord])
    def criteria_history(request:Request,run_id:str):
        return criteria_review().history(subject(request),run_id)

    @app.get(prefix+'/runs/{run_id}/criteria-versions/{version_id}',response_model=CriteriaVersionRecord)
    def criteria_version(request:Request,run_id:str,version_id:UUID):
        return criteria_review().get(subject(request),run_id,version_id)

    @app.post(prefix+'/runs/{run_id}/criteria-approvals',response_model=CriteriaApprovalRecord)
    async def approve_criteria(request:Request,run_id:str,idempotency_key:UUID=Header(alias='Idempotency-Key')):
        return await review_command(request,run_id,CriteriaApprovalRequest,criteria_review().approve,idempotency_key)

    @app.get(prefix+'/runs/{run_id}/criteria-approval',response_model=CriteriaApprovalRecord)
    def approved_criteria(request:Request,run_id:str):
        return criteria_review().approved(subject(request),run_id)

    @app.post(prefix+'/projects/{project_id}/document-versions/{version_id}/extractions',response_model=RunSnapshot,status_code=202)
    async def extract_pdf(request:Request,project_id:UUID,version_id:UUID,
                          idempotency_key:UUID=Header(alias='Idempotency-Key')):
        service=documents()
        actor=await run_in_threadpool(subject,request)
        await run_in_threadpool(service.get,actor,developer_allowed(request,settings),project_id,version_id)
        # No answers, model URL, actor IDs or poster placeholders from the client.
        async for chunk in request.stream():
            if chunk:raise AuthError('A02_EXTRACTION_BODY_NOT_ALLOWED',422)
        try:return await run_in_threadpool(app.state.framework.start_pdf,actor,project_id,version_id,idempotency_key)
        except PersistenceError:raise AuthError('A02_ADMISSION_UNCONFIRMED',503) from None

    async def text_document(request,pid,request_key,document_id=None,previous=None):
        from backend.app.routes.v1.business import parse
        service=documents();actor=await run_in_threadpool(subject,request)
        await run_in_threadpool(service.preflight,actor,developer_allowed(request,settings),pid,document_id,previous)
        if app.state.framework_revision not in ('AF004','AF005'):raise AuthError('FRAMEWORK_TEXT_SCHEMA_NOT_READY',503)
        body=await parse(request,TextDocumentInput)
        try:raw=body.text.encode('utf-8')
        except UnicodeError:raise AuthError('REQUIREMENT_TEXT_INVALID',422) from None
        return await run_in_threadpool(partial(service.register,actor,developer_allowed(request,settings),pid,request_key,raw,
            filename='requirements.txt',document_id=document_id,previous_version_id=previous,media_type='text/plain'))

    text_body_schema={'requestBody':{'required':True,'content':{'application/json':{'schema':TextDocumentInput.model_json_schema()}}}}
    @app.post(prefix+'/projects/{project_id}/text-documents',response_model=RegisteredDocument,status_code=201,openapi_extra=text_body_schema)
    async def register_text(request:Request,project_id:UUID,idempotency_key:UUID=Header(alias='Idempotency-Key')):
        return await text_document(request,project_id,idempotency_key)

    @app.post(prefix+'/projects/{project_id}/text-documents/{document_id}/versions',response_model=RegisteredDocument,status_code=201,openapi_extra=text_body_schema)
    async def register_text_version(request:Request,project_id:UUID,document_id:UUID,previous_version_id:UUID,
                                    idempotency_key:UUID=Header(alias='Idempotency-Key')):
        return await text_document(request,project_id,idempotency_key,document_id,previous_version_id)

    async def upload(request,pid,request_key,filename,document_id=None,previous=None):
        service=documents()
        actor=await run_in_threadpool(subject,request)
        dev=developer_allowed(request,settings)
        # Authorize before consuming any uploaded bytes; the transaction is
        # closed while streaming and rechecked when metadata is committed.
        await run_in_threadpool(service.preflight,actor,dev,pid,document_id,previous)
        if request.headers.get('content-type','').split(';',1)[0].strip().lower()!='application/pdf':
            raise AuthError('PDF_CONTENT_TYPE_REQUIRED',415)
        limit=FileLimits().max_document_bytes
        length=request.headers.get('content-length')
        if length is not None:
            if not length.isascii() or not length.isdecimal():raise AuthError('PDF_LENGTH_INVALID',400)
            if len(length)>10 or int(length)>limit:raise AuthError('FILE_TOO_LARGE',413)
        raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>limit:raise AuthError('FILE_TOO_LARGE',413)
            raw.extend(chunk)
        return await run_in_threadpool(partial(service.register,actor,dev,pid,request_key,bytes(raw),
            filename=filename,document_id=document_id,previous_version_id=previous))

    @app.post(prefix+'/projects/{project_id}/documents',response_model=RegisteredDocument,status_code=201)
    async def register_document(request:Request,project_id:UUID,filename:str='document.pdf',
                                idempotency_key:UUID=Header(alias='Idempotency-Key')):
        return await upload(request,project_id,idempotency_key,filename)

    @app.post(prefix+'/projects/{project_id}/documents/{document_id}/versions',response_model=RegisteredDocument,status_code=201)
    async def register_version(request:Request,project_id:UUID,document_id:UUID,previous_version_id:UUID,
                               filename:str='document.pdf',idempotency_key:UUID=Header(alias='Idempotency-Key')):
        return await upload(request,project_id,idempotency_key,filename,document_id,previous_version_id)

    @app.get(prefix+'/projects/{project_id}/document-versions/{version_id}',response_model=RegisteredDocument)
    def document_version(request:Request,project_id:UUID,version_id:UUID):
        return documents().get(subject(request),developer_allowed(request,settings),project_id,version_id)

    @app.get(prefix+'/projects/{project_id}/documents/{document_id}/versions',response_model=list[RegisteredDocument])
    def document_history(request:Request,project_id:UUID,document_id:UUID):
        return documents().history(subject(request),developer_allowed(request,settings),project_id,document_id)

    @app.get(prefix+'/projects/{project_id}/document-versions/{version_id}/content')
    def document_content(request:Request,project_id:UUID,version_id:UUID):
        record,raw=documents().content(subject(request),developer_allowed(request,settings),project_id,version_id)
        fallback='requirements.txt' if record.document.media_type=='text/plain' else 'document.pdf'
        return Response(raw,media_type=record.document.media_type,headers={
            'Content-Disposition':f'attachment; filename="{fallback}"; filename*=UTF-8\'\''+quote(record.original_name,safe='')})
    return app
