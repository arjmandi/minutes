"""Distributed admission control (spec v3 §13).

Single Redis source of truth: a hash ``slots`` mapping ``call_id -> {owner, lease_expiry}``.
Capacity = count of non-expired members. Acquire is one atomic Lua script: reap expired, then
either renew (same owner), reject as a *conflict* (call_id already held by a different owner —
never evict the incumbent), reject as *at_capacity*, or admit.

``owner`` is a **per-connection** fencing token (``worker_id:uuid``), not the bare worker id, so
two connections claiming the same ``call_id`` collide instead of silently sharing/evicting a slot,
and only the owning connection can renew/release. The ``worker_id`` prefix lets the reconciler
reclaim a dead worker's slots.

``now`` is passed from the client (not Redis TIME) to keep the scripts deterministic.
"""

from __future__ import annotations

import enum
import json
import time
import uuid

from redis.asyncio import Redis
from redis.commands.core import AsyncScript

_ACQUIRE_LUA = """
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local cap = tonumber(ARGV[5])
local slots = redis.call('HGETALL', KEYS[1])
local active = 0
local existing = nil
for i = 1, #slots, 2 do
  local field = slots[i]
  local ok, v = pcall(cjson.decode, slots[i + 1])
  if ok and v.lease_expiry and tonumber(v.lease_expiry) > now then
    if field == ARGV[1] then existing = v else active = active + 1 end
  else
    redis.call('HDEL', KEYS[1], field)
  end
end
if existing ~= nil then
  if existing.owner ~= ARGV[2] then
    return 2  -- conflict: held by a different connection; do not evict
  end
  redis.call('HSET', KEYS[1], ARGV[1], cjson.encode({owner = ARGV[2], lease_expiry = now + ttl}))
  return 1  -- renew (same owner)
end
if active >= cap then
  return 0  -- at capacity
end
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode({owner = ARGV[2], lease_expiry = now + ttl}))
return 1
"""

_RENEW_LUA = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return 0 end
local ok, v = pcall(cjson.decode, raw)
if (not ok) or v.owner ~= ARGV[2] then return 0 end
v.lease_expiry = tonumber(ARGV[3]) + tonumber(ARGV[4])
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(v))
return 1
"""

_RELEASE_LUA = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return 0 end
local ok, v = pcall(cjson.decode, raw)
if (not ok) or v.owner ~= ARGV[2] then return 0 end
redis.call('HDEL', KEYS[1], ARGV[1])
return 1
"""


class AcquireResult(enum.Enum):
    ADMITTED = "admitted"
    AT_CAPACITY = "at_capacity"
    CONFLICT = "conflict"


def _now_ms() -> int:
    return int(time.time() * 1000)


class CallRegistry:
    """Redis-backed admission cap shared across all workers."""

    def __init__(
        self,
        redis: Redis,
        *,
        worker_id: str,
        cap: int,
        lease_ttl_s: int,
        key: str = "minutes:admission:slots",
    ) -> None:
        self._redis = redis
        self._worker_id = worker_id
        self._cap = cap
        self._lease_ttl_ms = lease_ttl_s * 1000
        self._key = key
        self._acquire: AsyncScript = redis.register_script(_ACQUIRE_LUA)
        self._renew: AsyncScript = redis.register_script(_RENEW_LUA)
        self._release: AsyncScript = redis.register_script(_RELEASE_LUA)

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def mint_owner(self) -> str:
        """A per-connection fencing token: ``worker_id:uuid``."""
        return f"{self._worker_id}:{uuid.uuid4().hex}"

    async def acquire(self, call_id: str, owner: str) -> AcquireResult:
        res = int(
            await self._acquire(
                keys=[self._key],
                args=[call_id, owner, _now_ms(), self._lease_ttl_ms, self._cap],
            )
        )
        if res == 1:
            return AcquireResult.ADMITTED
        if res == 2:
            return AcquireResult.CONFLICT
        return AcquireResult.AT_CAPACITY

    async def renew(self, call_id: str, owner: str) -> bool:
        """Extend the lease. False if ownership was lost (caller must self-abort)."""
        res = await self._renew(
            keys=[self._key], args=[call_id, owner, _now_ms(), self._lease_ttl_ms]
        )
        return bool(res)

    async def release(self, call_id: str, owner: str) -> bool:
        """Release the slot; only the owning connection can (fencing)."""
        res = await self._release(keys=[self._key], args=[call_id, owner])
        return bool(res)

    async def active_count(self) -> int:
        """Non-expired slot count (read-only; does not reap)."""
        now = _now_ms()
        raw = await self._redis.hgetall(self._key)
        count = 0
        for value in raw.values():
            try:
                slot = json.loads(value)
            except (ValueError, TypeError):
                continue
            if slot.get("lease_expiry", 0) > now:
                count += 1
        return count
