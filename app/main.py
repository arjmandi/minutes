"""FastAPI application factory + lifespan wiring."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.admission.registry import CallRegistry
from app.api import auth, health, ingest
from app.config import DEV_ENVS, Settings, get_settings
from app.db.base import make_engine, make_session_factory
from app.logging import configure_logging, get_logger
from app.transcribe.factory import make_transcriber_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        log = get_logger("startup")
        engine = make_engine(settings.database_url)
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        worker_id = uuid.uuid4().hex

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        app.state.redis = redis
        app.state.worker_id = worker_id
        app.state.registry = CallRegistry(
            redis,
            worker_id=worker_id,
            cap=settings.max_concurrent_calls,
            lease_ttl_s=settings.lease_ttl_s,
        )
        app.state.transcriber_factory = make_transcriber_factory(settings)

        log.info("startup.complete", worker_id=worker_id, env=settings.app_env)
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()
            log.info("shutdown.complete", worker_id=worker_id)

    app = FastAPI(title="minutes", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    # The dev token-mint surface only exists in dev environments (defense beyond the 404 guard).
    if settings.app_env in DEV_ENVS:
        app.include_router(auth.router)
    app.include_router(ingest.router)
    return app


app = create_app()
