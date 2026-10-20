"""C09 HTTP projection. Blocking DB/file work is contained in worker calls."""
from uuid import UUID
from fastapi import APIRouter, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser, MultiPartException
from starlette.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from ...contracts.business import ProjectCreate, ProjectPatch, SetCreate, DraftCreate, DraftPut, Confirm
from ...hashing.canonical import loads, MAX_BYTES
from ...security.csrf import developer_allowed
from ...security.errors import AuthError
from ...storage.local import StorageError
from ...storage.images import MAX_IMAGE
from .auth import envelope


def identifier(value):
    try: return UUID(value)
    except (ValueError, TypeError): raise AuthError('INVALID_REQUEST', 422) from None


def subject(request):
    settings = request.app.state.settings
    dev = developer_allowed(request, settings)
    principal = request.app.state.auth.current(request.cookies.get(settings.session_cookie), developer_allowed=dev)
    if principal is None: raise AuthError('AUTHENTICATION_REQUIRED', 401)
    return principal, dev


async def parse(request, model):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise AuthError('UNSUPPORTED_MEDIA_TYPE', 415)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BYTES: raise AuthError('PAYLOAD_TOO_LARGE', 413)
        raw.extend(chunk)
    try: return model.model_validate(loads(raw))
    except ValidationError: raise AuthError('INVALID_REQUEST', 422) from None


def listing(request, kind, pid=None):
    allowed = {'limit', 'cursor'}
    if kind == 'projects': allowed.add('archived')
    if kind in ('criteria-drafts', 'criteria-versions'): allowed.update(('image_id', 'criteria_set_id'))
    query = request.query_params
    if any(key not in allowed or len(query.getlist(key)) != 1 for key in query): raise AuthError('INVALID_REQUEST', 422)
    raw_limit = query.get('limit', '20')
    if not raw_limit.isascii() or not raw_limit.isdecimal() or not 1 <= int(raw_limit) <= 100: raise AuthError('INVALID_REQUEST', 422)
    filters = {}
    if kind == 'projects':
        filters['archived'] = query.get('archived', 'exclude')
        if filters['archived'] not in ('exclude', 'include', 'only'): raise AuthError('INVALID_REQUEST', 422)
    for field in ('image_id', 'criteria_set_id'):
        if field in allowed and field in query: filters[field] = str(identifier(query[field]))
    who, dev = subject(request)
    return envelope(request, request.app.state.business.listing(who, dev, kind, pid, filters, int(raw_limit), query.get('cursor')))


def created(request, data, location):
    return JSONResponse(envelope(request, data), status_code=201, headers={'Location': location})


def router():
    result = APIRouter(prefix='/api/v1')

    @result.get('/projects')
    def projects(request: Request): return listing(request, 'projects')

    @result.post('/projects')
    async def create_project(request: Request):
        body = await parse(request, ProjectCreate)
        who, dev = await run_in_threadpool(subject, request)
        data = await run_in_threadpool(request.app.state.business.create_project, who, dev, body, request.state.request_id)
        return created(request, data, '/api/v1/projects/' + data['id'])

    @result.get('/projects/{project_id}')
    def get_project(request: Request, project_id: str):
        return envelope(request, request.app.state.business.get_project(*subject(request), identifier(project_id)))

    @result.patch('/projects/{project_id}')
    async def patch_project(request: Request, project_id: str):
        body = await parse(request, ProjectPatch)
        who, dev = await run_in_threadpool(subject, request)
        return envelope(request, await run_in_threadpool(request.app.state.business.patch_project, who, dev, identifier(project_id), body, request.state.request_id))

    @result.get('/projects/{project_id}/images')
    def images(request: Request, project_id: str): return listing(request, 'images', identifier(project_id))

    @result.post('/projects/{project_id}/images')
    async def upload(request: Request, project_id: str):
        pid = identifier(project_id)
        who, dev = await run_in_threadpool(subject, request)
        await run_in_threadpool(request.app.state.files.preflight, who, dev, pid)
        # Bound the parser stream, including multipart framing, before spooling each chunk.
        content_type = request.headers.get('content-type', '')
        if not content_type.lower().startswith('multipart/form-data;'): raise AuthError('UNSUPPORTED_MEDIA_TYPE', 415)
        class UploadTooLarge(MultiPartException): pass
        async def bounded_stream():
            count = 0
            async for chunk in request.stream():
                count += len(chunk)
                if count > MAX_IMAGE + 262144: raise UploadTooLarge('Multipart byte limit')
                yield chunk
        parser = MultiPartParser(request.headers, bounded_stream(), max_files=1, max_fields=0, max_part_size=1024)
        form = None
        try:
            form = await parser.parse()
            if list(form.keys()) != ['image'] or len(form.getlist('image')) != 1 or not isinstance(form['image'], UploadFile):
                raise AuthError('INVALID_REQUEST', 422)
            data = await run_in_threadpool(request.app.state.files.upload, who, dev, pid, form['image'].file, request.state.request_id)
        except UploadTooLarge: raise AuthError('PAYLOAD_TOO_LARGE', 413) from None
        except MultiPartException: raise AuthError('INVALID_REQUEST', 422) from None
        finally:
            if form is not None: await form.close()
            else:
                # Starlette 1.7 closes these on parse errors; also close on disconnect/cancellation.
                for temporary in parser._files_to_close_on_error: temporary.close()
        return created(request, data, '/api/v1/images/' + data['id'])

    @result.get('/images/{image_id}')
    def image(request: Request, image_id: str):
        return envelope(request, request.app.state.business.get_image(*subject(request), identifier(image_id)))

    @result.get('/images/{image_id}/content')
    def content(request: Request, image_id: str):
        file = request.app.state.business.get_image(*subject(request), identifier(image_id), content=True)
        try: stream = request.app.state.storage.verified(file['storage_key'], file['byte_size'], file['sha256'])
        except (StorageError, OSError): raise AuthError('FILE_UNAVAILABLE', 503) from None
        def chunks():
            try:
                while data := stream.read(65536): yield data
            finally: stream.close()
        return StreamingResponse(chunks(), media_type='image/png', headers={'Content-Length': str(file['byte_size']),
            'X-Content-Type-Options': 'nosniff', 'Content-Disposition': 'inline; filename="' + str(identifier(image_id)) + '.png"'},
            background=BackgroundTask(stream.close))

    @result.get('/projects/{project_id}/criteria-sets')
    def sets(request: Request, project_id: str): return listing(request, 'criteria-sets', identifier(project_id))

    @result.post('/projects/{project_id}/criteria-sets')
    async def create_set(request: Request, project_id: str):
        body = await parse(request, SetCreate); who, dev = await run_in_threadpool(subject, request)
        data = await run_in_threadpool(request.app.state.business.create_set, who, dev, identifier(project_id), body, request.state.request_id)
        return created(request, data, '/api/v1/projects/' + project_id + '/criteria-sets')

    @result.get('/projects/{project_id}/criteria-drafts')
    def drafts(request: Request, project_id: str): return listing(request, 'criteria-drafts', identifier(project_id))

    @result.post('/projects/{project_id}/criteria-drafts')
    async def create_draft(request: Request, project_id: str):
        body = await parse(request, DraftCreate); who, dev = await run_in_threadpool(subject, request)
        data = await run_in_threadpool(request.app.state.business.create_draft, who, dev, identifier(project_id), body, request.state.request_id)
        return created(request, data, '/api/v1/projects/' + project_id + '/criteria-drafts/' + data['id'])

    @result.get('/projects/{project_id}/criteria-drafts/{draft_id}')
    def draft(request: Request, project_id: str, draft_id: str):
        return envelope(request, request.app.state.business.get_draft(*subject(request), identifier(project_id), identifier(draft_id)))

    @result.put('/projects/{project_id}/criteria-drafts/{draft_id}')
    async def put_draft(request: Request, project_id: str, draft_id: str):
        body = await parse(request, DraftPut); who, dev = await run_in_threadpool(subject, request)
        return envelope(request, await run_in_threadpool(request.app.state.business.put_draft, who, dev, identifier(project_id), identifier(draft_id), body, request.state.request_id))

    @result.get('/projects/{project_id}/criteria-versions')
    def versions(request: Request, project_id: str): return listing(request, 'criteria-versions', identifier(project_id))

    @result.get('/projects/{project_id}/criteria-versions/{criteria_version_id}')
    def version(request: Request, project_id: str, criteria_version_id: str):
        return envelope(request, request.app.state.business.get_version(*subject(request), identifier(project_id), identifier(criteria_version_id)))

    @result.post('/projects/{project_id}/criteria-versions')
    async def confirm(request: Request, project_id: str):
        body = await parse(request, Confirm); who, dev = await run_in_threadpool(subject, request)
        keys = request.headers.getlist('Idempotency-Key')
        if len(keys) != 1: raise AuthError('INVALID_IDEMPOTENCY_KEY', 422)
        key = identifier(keys[0])
        data, replayed = await run_in_threadpool(request.app.state.business.confirm, who, dev, identifier(project_id), body, key, request.state.request_id)
        return JSONResponse(envelope(request, data), status_code=200 if replayed else 201,
            headers={'Idempotency-Replayed': 'true'} if replayed else {'Location': '/api/v1/projects/' + project_id + '/criteria-versions/' + data['id']})

    return result
