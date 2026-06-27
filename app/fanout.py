"""Live fan-out via Redis Streams (spec v3 §12).

The owning worker XADDs interim/final transcript + translation events to a per-meeting stream;
the live WebSocket relays them to subscribed clients. Best-effort: a fan-out failure must never
break the transcription pipeline. Clients backfill durable finals via the REST transcript endpoint
(ordered by meeting_seq), then go live from the stream.
"""

from __future__ import annotations

import json
import uuid

from redis.asyncio import Redis

from app.logging import get_logger

log = get_logger("fanout")

_MAXLEN = 1000  # cap the stream; durable history lives in Postgres


def stream_key(meeting_id: uuid.UUID) -> str:
    return f"minutes:stream:{meeting_id}"


async def publish(redis: Redis, meeting_id: uuid.UUID, event: dict) -> None:
    try:
        await redis.xadd(
            stream_key(meeting_id), {"data": json.dumps(event)}, maxlen=_MAXLEN, approximate=True
        )
    except Exception as exc:  # noqa: BLE001 — fan-out is best-effort
        log.warning("fanout.publish_failed", error=repr(exc))
