"""The Punchwalk FastAPI app: one service, one URL (API + media + the built PWA).

    uv run uvicorn punchwalk.api.app:app --reload --port 8000     # make dev

Startup (lifespan): create the SQLite (WAL) database under DATA_DIR, put jobs interrupted by a
restart back in the queue, start the in-process job worker (`PUNCHWALK_WORKER=0` disables it), and,
with DEMO_RESEED=true, the demo re-seed (boot + nightly at 03:00 America/New_York; api/reseed.py).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from punchwalk import __version__
from punchwalk.api import schemas as S
from punchwalk.api.errors import install_error_handlers
from punchwalk.api.guards import BodySizeLimitMiddleware
from punchwalk.api.jobs import recover_interrupted_jobs, start_worker, stop_worker
from punchwalk.api.media import router as media_router
from punchwalk.api.media import spa_router
from punchwalk.api.reseed import start_reseed, stop_reseed
from punchwalk.api.routes import items, projects, trades, walks
from punchwalk.config import get_settings
from punchwalk.core.enums import ReasoningProvider
from punchwalk.db import init_db

log = logging.getLogger("punchwalk.api")

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    resumed = recover_interrupted_jobs()
    if resumed:
        log.info("re-queued %d job(s) interrupted by a restart", resumed)
    start_worker()
    start_reseed()
    try:
        yield
    finally:
        stop_reseed()
        stop_worker()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Punchwalk",
        version=__version__,
        description="Turn a construction punch walk into a verified, dispatched punch list.",
        lifespan=lifespan,
    )
    # Order: the last middleware added is the outermost, so CORS headers also go on 413s.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
    )
    install_error_handlers(app)

    @app.get("/healthz", response_model=S.HealthOut, tags=["health"])
    def healthz() -> S.HealthOut:
        settings = get_settings()
        return S.HealthOut(
            ok=True,
            version=__version__,
            demo_mode=settings.DEMO_MODE,
            reasoning_provider=ReasoningProvider(settings.REASONING_PROVIDER),
            time=datetime.now(UTC),
        )

    app.include_router(projects.router)
    app.include_router(trades.router)
    app.include_router(walks.router)
    app.include_router(items.router)
    app.include_router(media_router)
    app.include_router(spa_router)  # last: catch-all for the PWA's client-side routes
    return app


app = create_app()
