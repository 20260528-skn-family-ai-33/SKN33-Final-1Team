"""Local video service sharing the framework's PostgreSQL session boundary."""
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from threading import Lock

from starlette.concurrency import run_in_threadpool

from backend.app.config import ConfigurationError
from backend.app.main import create_app as create_auth_app
from backend.app.routes.v1.business import subject
from backend.app.video.logos.routes import video_logo_router
from backend.app.video.review import recover_jobs, video_review_router


def create_video_app(settings, root: Path, *, engine=None, readiness=None):
    """Keep video files separate from legacy history and the poster store.

    A caller must ensure only one service uses this root. This entrypoint is
    deliberately limited to local development; it is not an AWS deployment.
    """
    if settings.environment not in ('development', 'test'):
        raise ConfigurationError('The local video service requires development or test mode.')
    settings = replace(settings, storage_root=None, frontend_dist=None,
                       review_pipeline='disabled', review_executor='background_tasks',
                       review_config_file=None)
    root = Path(root)
    reviews, logos = root / 'reviews', root / 'logos'
    reviews.mkdir(mode=0o700, parents=True, exist_ok=True)
    logos.mkdir(mode=0o700, parents=True, exist_ok=True)
    app = create_auth_app(settings=settings, engine=engine)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(current):
        async with original(current):
            await run_in_threadpool(recover_jobs, reviews)
            yield

    app.router.lifespan_context = lifespan
    inference_lock = Lock()
    app.include_router(video_review_router(root=reviews, inference_lock=inference_lock,
                                           authenticate=subject, readiness=readiness,
                                           allowed_hosts=settings.hosts))
    app.include_router(video_logo_router(root=logos, inference_lock=inference_lock,
                                         authenticate=subject, allowed_hosts=settings.hosts))
    app.title = 'Creative Checker local video service'
    return app
