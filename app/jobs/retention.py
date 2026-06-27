"""Retention job (spec v3 §15): delete meetings (and their Spaces objects) older than
``retention_days``. DB FK cascade removes sessions/segments/translations/chunks.

Run as a one-shot (k8s CronJob): ``python -m app.jobs.retention``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.logging import configure_logging, get_logger
from app.storage.factory import make_storage


async def run() -> int:
    settings = get_settings()
    configure_logging()
    log = get_logger("retention")
    if settings.retention_days < 1:  # belt-and-suspenders (Settings also enforces ge=1)
        log.error("retention.refused_invalid_days", retention_days=settings.retention_days)
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=settings.retention_days)
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    storage = make_storage(settings)
    deleted = 0
    try:
        while True:
            async with factory() as db:
                ids = await repo.expired_meeting_ids(db, before=cutoff)
            if not ids:
                break
            before = deleted
            for meeting_id in ids:
                async with factory() as db:
                    keys = await repo.chunk_keys_for_meeting(db, meeting_id)
                for key in keys:
                    try:
                        await storage.delete(key)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("retention.object_delete_failed", key=key, error=repr(exc))
                async with factory() as db:
                    if await repo.delete_meeting(db, meeting_id):
                        deleted += 1
                    await db.commit()
            if deleted == before:  # no forward progress -> avoid an infinite re-query loop
                log.error("retention.no_progress", candidates=len(ids))
                break
    finally:
        await engine.dispose()
    log.info("retention.done", deleted=deleted, cutoff=cutoff.isoformat())
    return deleted


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
