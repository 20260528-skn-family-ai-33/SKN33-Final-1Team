"""Explicit v1 authentication and optional C09/C10 business app. No legacy routes, seed, migration or model startup."""
from contextlib import asynccontextmanager
import logging
from uuid import uuid4
from fastapi import FastAPI,Request
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from .config import ConfigurationError
from .v1_config import load_v1_settings
from .security.errors import AuthError

logger=logging.getLogger('creative_checker.security')


def create_app(*,settings=None,engine=None,review_adapter=None):
    from .security.logging import configure_security_logging
    configure_security_logging()
    try:
        if settings is None:
            import os
            from pathlib import Path
            from .config import read_environment_file
            selected=os.environ.get('CC_APP_ENV_FILE')
            settings=load_v1_settings(read_environment_file(Path(selected)) if selected else None)
    except ConfigurationError:
        logger.warning('profile_rejected',extra={'event':'profile_rejected','reason':'APP_CONFIGURATION_INVALID'})
        raise
    if review_adapter is not None and settings.environment != "test":
        raise ConfigurationError("Injected review adapters are test-only.")
    owned=engine is None

    @asynccontextmanager
    async def lifespan(app):
        from .persistence.database import create_app_engine
        from .persistence.schema_inventory import check_schema
        from .persistence.transaction import unit_of_work
        from .security.passwords import Passwords
        from .security.csrf import Csrf
        from .security.limits import LoginLimits
        from .services.auth import AuthService
        from .services.authorization import Authorization
        def prepare():
            if settings.frontend_dist is not None:
                from .web import prepare_build
                prepare_build(settings.frontend_dist)
            check_schema(app.state.engine,settings)
            if settings.secure:
                with unit_of_work(app.state.engine) as connection:
                    if connection.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM public.users WHERE account_kind='developer' AND is_active)").scalar_one():
                        raise ConfigurationError('Production requires all developer users to be inactive before startup.')
            return Passwords()
        app.state.engine=engine or create_app_engine(settings)
        try:
            passwords=await run_in_threadpool(prepare)
            app.state.csrf=Csrf(settings.csrf_secret)
            app.state.auth=AuthService(app.state.engine,settings,passwords,LoginLimits(settings.csrf_secret))
            app.state.authorization=Authorization(settings)
            if settings.storage_root is not None:
                def prepare_storage():
                    import sys
                    from .storage.local import LocalStorage
                    from .services.business import Business
                    from .services.files import Files
                    if settings.secure and sys.platform != 'linux':
                        raise ConfigurationError('Production file storage requires a single Linux host filesystem.')
                    app.state.storage=LocalStorage(settings.storage_root)
                    durable=settings.review_executor=='postgres_worker'
                    app.state.storage.start(shared=durable)
                    app.state.business=Business(app.state.engine,app.state.authorization,settings)
                    app.state.files=Files(app.state.engine,app.state.storage,app.state.business)
                    app.state.recovery=app.state.files.recover(concurrent=durable)
                    from .execution.pipeline import DisabledPipeline, PaddlePipeline
                    from .services.reviews import Reviews
                    from .services.review_queries import ReviewQueries
                    from .services.review_results import ReviewExecutor
                    from .execution.leader import Leader
                    if durable:
                        from .execution.worker_config import QueuedPipeline
                        adapter=review_adapter or QueuedPipeline(settings.review_config_file)
                    else:adapter=review_adapter or (PaddlePipeline() if settings.review_pipeline=='paddle-only' else DisabledPipeline())
                    app.state.reviews=Reviews(app.state.engine,app.state.business,app.state.storage,adapter,executor_kind=settings.review_executor)
                    app.state.review_queries=ReviewQueries(app.state.reviews)
                    app.state.review_executor=None
                    if not durable and (review_adapter is not None or settings.review_pipeline!='disabled'):
                        app.state.review_leader=Leader(app.state.engine)
                        app.state.reviews.leader=app.state.review_leader
                        app.state.review_executor=ReviewExecutor(app.state.reviews,app.state.review_leader)
                        app.state.result_recovery=app.state.review_executor.recover()
                        app.state.interrupted_runs=app.state.reviews.recover_runs()
                await run_in_threadpool(prepare_storage)
            app.state.ready=True
            yield
        except Exception:
            logger.error('profile_startup_rejected',extra={'event':'profile_startup_rejected'})
            raise
        finally:
            app.state.ready=False
            if hasattr(app.state,'review_leader'):await run_in_threadpool(app.state.review_leader.close)
            if hasattr(app.state,'storage'):await run_in_threadpool(app.state.storage.close)
            if owned:await run_in_threadpool(app.state.engine.dispose)

    app=FastAPI(title='Creative Checker v1 authentication',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.settings=settings;app.state.ready=False

    @app.middleware('http')
    async def boundary(request:Request,call_next):
        request.state.request_id=str(uuid4())
        try:
            if request.url.hostname not in settings.hosts:raise AuthError('RESOURCE_NOT_FOUND',404)
            if settings.secure and request.url.scheme!='https':raise AuthError('HTTPS_REQUIRED',400)
            from .legacy_boundary import review_path,retired_response
            if review_path(request.url.path):
                if request.method not in ('GET','HEAD','OPTIONS'):
                    response=retired_response(request.state.request_id)
                    from .web import headers
                    headers(response,secure=settings.secure)
                    return response
                raise AuthError('RESOURCE_NOT_FOUND',404)
            if not app.state.ready:raise AuthError('AUTH_UNAVAILABLE',503)
            if request.url.path!='/api/v1/health':
                from .management.maintenance import active
                from .persistence.database import PersistenceError
                try:maintenance=await run_in_threadpool(active,app.state.engine)
                except PersistenceError:raise AuthError('DATABASE_UNAVAILABLE',503) from None
                if maintenance:raise AuthError('MAINTENANCE_ACTIVE',503,retry_after=1)
            if request.method not in ('GET','HEAD','OPTIONS'):app.state.csrf.verify(request,settings)
            response=await call_next(request)
        except AuthError as error:response=error_response(request,error)
        from .web import headers
        headers(response,secure=settings.secure,modeltest=request.url.path.rstrip('/') == '/modeltest')
        response.headers['Cache-Control']='no-store'
        response.headers['X-Request-ID']=request.state.request_id
        return response

    def error_response(request,error):
        logger.info('security_request_rejected',extra={'event':'security_request_rejected','request_id':request.state.request_id,'reason':error.code})
        response=JSONResponse({'error':{'code':error.code,'message':str(error),'fields':{}},
            'meta':{'contract_version':'cc-api-v1','request_id':request.state.request_id}},status_code=error.status)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Request-ID']=request.state.request_id
        from .web import headers
        headers(response,secure=settings.secure)
        if error.retry_after is not None:response.headers['Retry-After']=str(error.retry_after)
        return response

    @app.exception_handler(AuthError)
    async def auth_error(request,error):return error_response(request,error)

    @app.exception_handler(Exception)
    async def unexpected_error(request,error):
        logger.error('request_failed',extra={'event':'request_failed','request_id':request.state.request_id})
        return error_response(request,AuthError('INTERNAL_ERROR',500))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,error):return error_response(request,AuthError('INVALID_AUTH_REQUEST',422))

    from .routes.v1.auth import router,envelope
    app.include_router(router())
    if not settings.secure and settings.environment in ('development', 'test'):
        from pathlib import Path
        from .modeltest_ssh import modeltest_ssh_router
        app.include_router(modeltest_ssh_router(Path(__file__).resolve().parents[2] / '.runtime' / 'modeltest-ssh'))

    if settings.storage_root is not None:
        from .routes.v1.business import router as business_router
        app.include_router(business_router())
        from .routes.v1.reviews import router as review_router
        app.include_router(review_router())

    @app.get('/api/v1/health')
    def health(request:Request):
        from datetime import datetime,timezone
        from .persistence.transaction import unit_of_work
        from .persistence.database import PersistenceError
        checked=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        database='ready';maintenance=False
        try:
            from .management.maintenance import active
            maintenance=active(app.state.engine)
            with unit_of_work(app.state.engine) as connection:connection.exec_driver_sql('SELECT 1')
        except PersistenceError:database='not_ready'
        checks={name:{'state':'unknown','checked_at':None} for name in ('worker','ocr_runtime','model','storage')}
        if settings.storage_root is not None:
            checks['storage']={'state':'ready','checked_at':checked}
            if app.state.review_executor is not None:
                try:
                    app.state.review_leader.check()
                    current_config=app.state.reviews.adapter.describe()
                    verified=app.state.review_executor.last_success_config==current_config
                    checks['ocr_runtime']={'state':'ready' if verified else 'unknown','checked_at':checked if verified else None}
                    checks['model']={'state':'ready','checked_at':checked}
                except Exception:
                    checks['ocr_runtime']={'state':'not_ready','checked_at':checked}
        checks.update(api={'state':'not_ready' if maintenance else 'ready','checked_at':checked},db={'state':database,'checked_at':checked})
        return envelope(request,{'status':'not_ready' if maintenance or database!='ready' else 'degraded','maintenance':maintenance,'scope':'business' if settings.storage_root else 'authentication',
            'checks':checks,'limits':{'login_json_bytes':4096,'sample_available':False}})

    if settings.frontend_dist is not None:
        from .web import install_routes
        install_routes(app,settings.frontend_dist)

    return app
