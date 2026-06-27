"""Admission registry tests (spec v3 §13): cap, release, per-connection ownership/conflict.

Requires a reachable Redis (``docker compose up -d redis``); skips otherwise.
"""

from __future__ import annotations

import os
import uuid

import pytest
from redis.asyncio import Redis

from app.admission.registry import AcquireResult, CallRegistry

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


def _reg(redis, key, *, cap=5, worker="w1") -> CallRegistry:
    return CallRegistry(redis, worker_id=worker, cap=cap, lease_ttl_s=60, key=key)


async def test_cap_enforced(redis, key):
    reg = _reg(redis, key, cap=2)
    assert await reg.acquire("c1", "o1") is AcquireResult.ADMITTED
    assert await reg.acquire("c2", "o2") is AcquireResult.ADMITTED
    assert await reg.acquire("c3", "o3") is AcquireResult.AT_CAPACITY
    assert await reg.active_count() == 2
    await redis.delete(key)


async def test_release_frees_slot(redis, key):
    reg = _reg(redis, key, cap=1)
    assert await reg.acquire("c1", "o1") is AcquireResult.ADMITTED
    assert await reg.acquire("c2", "o2") is AcquireResult.AT_CAPACITY
    assert await reg.release("c1", "o1")
    assert await reg.acquire("c2", "o2") is AcquireResult.ADMITTED
    await redis.delete(key)


async def test_same_owner_renews(redis, key):
    reg = _reg(redis, key, cap=1)
    assert await reg.acquire("c1", "o1") is AcquireResult.ADMITTED
    assert await reg.acquire("c1", "o1") is AcquireResult.ADMITTED  # renew, not a new slot
    assert await reg.active_count() == 1
    await redis.delete(key)


async def test_duplicate_call_id_conflicts_without_eviction(redis, key):
    # A second connection (different owner) for the same call_id must NOT evict the incumbent.
    reg = _reg(redis, key, cap=5)
    assert await reg.acquire("c1", "ownerA") is AcquireResult.ADMITTED
    assert await reg.acquire("c1", "ownerB") is AcquireResult.CONFLICT
    assert not await reg.release("c1", "ownerB")  # non-owner can't release
    assert not await reg.renew("c1", "ownerB")  # non-owner can't renew
    assert await reg.renew("c1", "ownerA")  # incumbent still holds it
    assert await reg.release("c1", "ownerA")
    await redis.delete(key)


async def test_release_requires_owner(redis, key):
    reg = _reg(redis, key, cap=5)
    assert await reg.acquire("c1", "ownerA") is AcquireResult.ADMITTED
    assert not await reg.release("c1", "ownerX")
    assert await reg.release("c1", "ownerA")
    await redis.delete(key)


async def test_mint_owner_is_per_connection(redis, key):
    reg = _reg(redis, key)
    assert reg.mint_owner() != reg.mint_owner()
    assert reg.mint_owner().startswith("w1:")
