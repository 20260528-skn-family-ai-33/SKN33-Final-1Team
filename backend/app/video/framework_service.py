"""Video service sharing the framework's PostgreSQL session boundary."""
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from threading import Lock
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from backend.app.config import ConfigurationError
from backend.app.main import create_app as create_auth_app
from backend.app.poster.logos.routes import poster_logo_router
from backend.app.routes.v1.business import subject
from backend.app.video.logos.client import WorkerPool
from backend.app.video.logos.routes import video_logo_router
from backend.app.video.review import recover_jobs, video_review_router


def create_video_app(settings, root: Path, *, engine=None, readiness=None, aws_test=False,
                     worker_pool: WorkerPool | None = None):
    """Keep video files separate from legacy history and the poster store.

    A caller must ensure only one service uses this root.
    """
    if aws_test:
        if not settings.secure or settings.developer_login:
            raise ConfigurationError('The AWS test video service requires secure sessions.')
    elif settings.environment not in ('development', 'test'):
        raise ConfigurationError('The local video service requires development or test mode.')
    if worker_pool and not aws_test:
        raise ConfigurationError('The two-worker pool is only enabled in AWS test.')
    settings = replace(settings, storage_root=None, frontend_dist=None,
                       review_pipeline='disabled', review_executor='background_tasks',
                       review_config_file=None)
    root = Path(root)
    reviews, logos, poster_logos = root / 'reviews', root / 'logos', root / 'poster-logos'
    reviews.mkdir(mode=0o700, parents=True, exist_ok=True)
    logos.mkdir(mode=0o700, parents=True, exist_ok=True)
    poster_logos.mkdir(mode=0o700, parents=True, exist_ok=True)
    app = create_auth_app(settings=settings, engine=engine)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(current):
        async with original(current):
            await run_in_threadpool(recover_jobs, reviews, isolate_users=aws_test)
            yield

    app.router.lifespan_context = lifespan
    inference_lock = Lock()

    def authenticate(request):
        principal, developer = subject(request)
        if aws_test:
            request.state.video_owner = str(UUID(str(principal.user_id)))
        return principal, developer

    app.include_router(video_review_router(root=reviews, inference_lock=inference_lock,
                                           authenticate=authenticate, readiness=readiness,
                                           allowed_hosts=settings.hosts, isolate_users=aws_test, worker_pool=worker_pool))
    app.include_router(video_logo_router(root=logos, inference_lock=inference_lock,
                                         authenticate=authenticate, allowed_hosts=settings.hosts,
                                         isolate_users=aws_test, linked_reviews=reviews if aws_test else None,
                                         worker_pool=worker_pool))
    # 포스터 로고는 같은 RunPod 로고 워커(슬롯)와 추론 잠금을 쓴다. 포스터 검수 실행과는 run_id로만 연결한다.
    app.include_router(poster_logo_router(root=poster_logos, inference_lock=inference_lock,
                                          authenticate=authenticate, allowed_hosts=settings.hosts,
                                          isolate_users=aws_test, worker_pool=worker_pool))
    app.title = 'Creative Checker video service'
    return app
