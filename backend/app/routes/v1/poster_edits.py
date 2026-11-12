"""API-only poster editing, independent of the durable review worker.

문구 수정은 포스터 일부를 OpenAI 이미지 편집 API로 보낸다. 보안 자료(실행 model_route=runpod,
backend/agent_framework/model_route.py)는 외부로 보내지 않으므로 거절한다(2026-11-12 사용자 결정).
"""
import asyncio
from functools import partial

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from backend.agent_framework.contracts import AssetRef, RunSnapshot
from backend.agent_framework.model_route import route_of
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.poster_review import read_registered_poster

from ...persistence.transaction import unit_of_work
from ...security.errors import AuthError
from ...services.poster_edits import PosterTextEdit, edit_response
from .business import parse, subject

SECURE_ROUTE = 'runpod'


def edit_route(service, actor, run_id):
    """화면이 버튼을 막을 수 있게 실행의 모델 경로만 돌려준다(읽기 권한 확인)."""
    store = service.store
    with unit_of_work(store.engine) as connection:
        row = store._row(connection, run_id)
        snapshot = RunSnapshot.model_validate(row['snapshot_json'])
        store._access(connection, actor, snapshot.context, write=False)
        return route_of(row['input_json'])


def editable_asset(service, actor, run_id):
    store = service.store
    with unit_of_work(store.engine) as connection:
        row = store._row(connection, run_id)
        snapshot = RunSnapshot.model_validate(row['snapshot_json'])
        store._access(connection, actor, snapshot.context)
        if route_of(row['input_json']) == SECURE_ROUTE:
            raise AuthError('POSTER_EDIT_SECURE_MATERIAL', 403)
        asset = row['input_json'].get('asset')
        if (snapshot.execution_status not in ('waiting_review', 'waiting_revision', 'completed')
                or snapshot.validated is None or snapshot.baseline is None or asset is None):
            raise AuthError('POSTER_EDIT_RESULT_REQUIRED', 409)
        return snapshot.context, AssetRef.model_validate(asset)


def source_content(service, actor, context, asset):
    try:
        return read_registered_poster(service.store, service.storage, context, asset, actor).content
    except AdapterFailure:
        raise AuthError('POSTER_EDIT_SOURCE_UNAVAILABLE', 503) from None


def router():
    result = APIRouter(prefix='/api/framework/v1')
    capacity = asyncio.Semaphore(2)

    @result.get('/runs/{run_id}/poster-text-edits')
    async def edit_policy(request: Request, run_id: str):
        """이 실행에서 문구 수정을 쓸 수 있는지. 보안 자료면 allowed=false(서버 POST도 같은 이유로 거절한다)."""
        service = getattr(request.app.state, 'poster_reviews', None)
        if service is None:
            raise AuthError('RESOURCE_NOT_FOUND', 404)
        actor, _ = await run_in_threadpool(subject, request)
        route = await run_in_threadpool(edit_route, service, actor, run_id)
        secure = route == SECURE_ROUTE
        return {'allowed': not secure, 'reason': 'POSTER_EDIT_SECURE_MATERIAL' if secure else None}

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
