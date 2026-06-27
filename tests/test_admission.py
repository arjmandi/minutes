"""Admission registry tests (spec v3 §13): cap enforcement, release, fencing.

Requires a reachable Redis (``docker compose up -d redis``); skips otherwise.
"""

from __future__ import annotations

import os
import uuid

import pytest
from redis.asyncio import Redis

from app.admission.registry import CallRegistry

REDIS_URL = os.environ.get("MINUTES_REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def redis():
    client = Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("redis not available")
    yield client
    await client.aclose()


@pytest.fixture
def key() -> str:
    return f"test:admission:{uuid.uuid4().hex}"


async def test_cap_enforced(redis, key):
    reg = CallRegistry(redis, worker_id="w1", cap=2, lease_ttl_s=60, key=key)
    assert await reg.acquire("c1")
    assert await reg.acquire("c2")
    assert not await reg.acquire("c3")  # at capacity
    assert await reg.active_count() == 2
    await redis.delete(key)


async def test_release_frees_slot(redis, key):
    reg = CallRegistry(redis, worker_id="w1", cap=1, lease_ttl_s=60, key=key)
    assert await reg.acquire("c1")
    assert not await reg.acquire("c2")
    assert await reg.release("c1")
    assert await reg.acquire("c2")
    await redis.delete(key)


async def test_fencing_blocks_non_owner(redis, key):
    owner = CallRegistry(redis, worker_id="wA", cap=5, lease_ttl_s=60, key=key)
    other = CallRegistry(redis, worker_id="wB", cap=5, lease_ttl_s=60, key=key)
    assert await owner.acquire("c1")
    assert not await other.release("c1")  # only the owner may release
    assert not await other.renew("c1")  # only the owner may renew
    assert await owner.release("c1")
    await redis.delete(key)


async def test_acquire_is_idempotent_renew(redis, key):
    reg = CallRegistry(redis, worker_id="w1", cap=1, lease_ttl_s=60, key=key)
    assert await reg.acquire("c1")
    assert await reg.acquire("c1")  # same call -> renew, not a new slot
    assert await reg.active_count() == 1
    await redis.delete(key)
