"""FastAPI application factory + lifespan wiring."""

from __future__ import annotations

import mimetypes
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from app.admission.registry import CallRegistry
from app.api import accounts, auth, control, health, ingest, meetings, shared, uploads
from app.config import DEV_ENVS, Settings, get_settings
from app.db.base import make_engine, make_session_factory
from app.logging import configure_logging, get_logger
from app.storage.factory import make_storage
from app.transcribe.factory import make_transcriber_factory
from app.translate.factory import make_translator_factory


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
        app.state.translator_factory = make_translator_factory(settings)
        app.state.storage = make_storage(settings)

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
        app.include_router(auth.router, prefix="/api")  # /api/auth/dev-token (dev only)
    app.include_router(ingest.router)  # /ingest stays at root (capture WebSocket)
    app.include_router(meetings.router, prefix="/api")  # /api/meetings/*
    app.include_router(control.router, prefix="/api")  # /api/sessions/*
    app.include_router(accounts.router, prefix="/api")  # /api/auth/*, /api/me/*, /api/capture/token
    app.include_router(uploads.router, prefix="/api")  # /api/uploads/*
    app.include_router(shared.router, prefix="/api")  # /api/shared/* (anonymous public read)

    # The product SPA is served at / and /app; its static assets (CSS/JS) at /assets. A stock
    # self-host's root IS the app; the marketing site lives in the private deploy repo and is
    # path-routed in front of this.
    web_dir = Path(__file__).resolve().parent / "web"
    web_index = web_dir / "index.html"
    app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def root() -> FileResponse:
        return FileResponse(web_index)

    @app.get("/app", include_in_schema=False)
    async def viewer() -> FileResponse:
        return FileResponse(web_index)

    # Public share-link viewer: the SPA reads the token from the path and calls /api/shared/*.
    @app.get("/shared/{token}", include_in_schema=False)
    async def shared_viewer(token: str) -> FileResponse:
        return FileResponse(web_index)

    # PWA: the manifest + service worker must be served at ROOT scope (not under /assets, whose
    # scope is too narrow) so the installed app — start_url /app, scope / — can register the SW.
    mimetypes.add_type("application/manifest+json", ".webmanifest")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> FileResponse:
        return FileResponse(
            web_dir / "manifest.webmanifest", media_type="application/manifest+json"
        )

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        return FileResponse(
            web_dir / "sw.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache"},  # always revalidate the SW itself
        )

    return app


app = create_app()
