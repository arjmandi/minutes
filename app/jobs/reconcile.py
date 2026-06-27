"""Orphan reconciler (spec v3 §9): resolve PENDING audio chunks left by failed uploads/crashes.

A chunk whose session has ENDED/FAILED but is still PENDING means its upload never completed. For
each, HEAD the object: mark RECORDED if it exists, else LOST. Run via k8s CronJob.

    python -m app.jobs.reconcile
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.db.models import ChunkState
from app.logging import configure_logging, get_logger
from app.storage.factory import make_storage


async def run() -> tuple[int, int]:
    settings = get_settings()
    configure_logging()
    log = get_logger("reconcile")
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    storage = make_storage(settings)
    recorded = lost = 0
    try:
        while True:
            async with factory() as db:
                pending = await repo.pending_chunks_for_ended_sessions(db)
            if not pending:
                break
            progressed = False
            for chunk_id, key in pending:
                try:
                    exists = await storage.head(key)
                except Exception as exc:  # noqa: BLE001 — skip this one; don't spin
                    log.warning("reconcile.head_failed", key=key, error=repr(exc))
                    continue
                state = ChunkState.recorded if exists else ChunkState.lost
                async with factory() as db:
                    await repo.mark_chunk(db, chunk_id=chunk_id, state=state)
                    await db.commit()
                recorded += int(exists)
                lost += int(not exists)
                progressed = True
            if not progressed:  # every head failed this pass -> stop rather than loop
                break
    finally:
        await engine.dispose()
    log.info("reconcile.done", recorded=recorded, lost=lost)
    return recorded, lost


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
