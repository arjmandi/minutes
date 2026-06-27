"""Control-plane routing (spec v3 §8): deliver set_config to the worker owning a live call.

The control endpoint may land on any worker; the owning SessionManager subscribes to the call's
Redis channel and applies the change. Pub/sub is fire-and-forget by design — every accepted change
is also persisted to config_changes for audit/replay.
"""

from __future__ import annotations

import json

from redis.asyncio import Redis


def channel_key(call_id: str) -> str:
    return f"minutes:control:{call_id}"


async def publish(redis: Redis, call_id: str, config: dict) -> None:
    await redis.publish(channel_key(call_id), json.dumps(config))
