"""API-only poster editing, independent of the durable review worker."""
import asyncio
from functools import partial

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from backend.agent_framework.contracts import AssetRef, RunSnapshot
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.poster_review import read_registered_poster

from ...persistence.transaction import unit_of_work
from ...security.errors import AuthError
from ...services.poster_edits import PosterTextEdit, edit_response
from .business import parse, subject


def editable_asset(service, actor, run_id):
    store = service.store
    with unit_of_work(store.engine) as connection:
        row = store._row(connection, run_id)
        snapshot = RunSnapshot.model_validate(row['snapshot_json'])
        store._access(connection, actor, snapshot.context)
        if (snapshot.execution_status != 'waiting_revision' or snapshot.human_decision != 'changes_approved'
                or snapshot.baseline is None or row['input_json'].get('asset') is None):
            raise AuthError('POSTER_EDIT_APPROVAL_REQUIRED', 409)
        return snapshot.context, AssetRef.model_validate(row['input_json']['asset'])


def source_content(service, actor, context, asset):
    try:
        return read_registered_poster(service.store, service.storage, context, asset, actor).content
    except AdapterFailure:
        raise AuthError('POSTER_EDIT_SOURCE_UNAVAILABLE', 503) from None


def router():
    result = APIRouter(prefix='/api/framework/v1')
    capacity = asyncio.Semaphore(2)

    @result.post('/runs/{run_id}/poster-text-edits')
    async def edit(request: Request, run_id: str):
        service = getattr(request.app.state, 'poster_reviews', None)
        if service is None:
            raise AuthError('RESOURCE_NOT_FOUND', 404)
        actor, _ = await run_in_threadpool(subject, request)
        key = request.app.state.settings.poster_edit_key
        if key is None:
            raise AuthError('POSTER_EDIT_NOT_CONFIGURED', 503)
        body = await parse(request, PosterTextEdit)
        context, asset = await run_in_threadpool(editable_asset, service, actor, run_id)
        load_source = partial(source_content, service, actor, context, asset)
        return StreamingResponse(edit_response(load_source, body, key.reveal(), capacity),
                                 media_type='application/json', headers={'X-Accel-Buffering': 'no'})

    return result
