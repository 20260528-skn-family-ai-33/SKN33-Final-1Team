"""Fresh framework entrypoint reusing M008 authentication, CSRF and app Engine.

No OCR/background-task leader, frontend build, model initialization or migration.
POST admission/approval only queues work; the separate worker invokes adapters.
"""
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from backend.app.main import create_app as create_auth_app
from backend.app.persistence.transaction import unit_of_work
from backend.app.security.csrf import developer_allowed
from backend.app.security.errors import AuthError
from backend.app.config import ConfigurationError
from .api import StartRequest,RevisionRequest
from .contracts import Approval,RunSnapshot,StepTimeline
from .postgres import PostgresStore
from .postgres_schema import check_extension,check_app_permissions


def create_postgres_app(settings,configuration,*,engine=None):
    if settings.environment not in ('development','test'):
        raise ConfigurationError('The initial framework PostgreSQL profile supports development/test only.')
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
                    check_extension(c);check_app_permissions(c)
                return PostgresStore(current.state.engine,configuration,current.state.authorization)
            current.state.framework=await run_in_threadpool(prepare)
            yield
    app.router.lifespan_context=lifespan
    app.title='Creative Checker — PostgreSQL agent framework'

    def subject(request):
        user=app.state.auth.current(request.cookies.get(settings.session_cookie),
            developer_allowed=developer_allowed(request,settings))
        if user is None:raise AuthError('AUTHENTICATION_REQUIRED',401)
        return user

    def invoke(action,*args):
        try:return action(*args)
        except ValueError:raise AuthError('FRAMEWORK_CONFLICT',409) from None

    prefix='/api/framework/v1'
    @app.get(prefix+'/health')
    def health():
        return {'storage':'postgres','queue':'agent_framework.jobs','schema':'AF001',
                'authentication':'existing_M008_session_and_CSRF','adapters_initialized_in_api':False,'production_ready':False}
    @app.post(prefix+'/runs',response_model=RunSnapshot,status_code=202)
    def start(request:Request,body:StartRequest):
        return invoke(app.state.framework.start,subject(request),body.context,body.document,body.asset)
    @app.get(prefix+'/runs/{run_id}',response_model=RunSnapshot)
    def read(request:Request,run_id:str):return invoke(app.state.framework.get,subject(request),run_id)
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
    return app
