"""Distributed admission control (spec v3 §13).

Single Redis source of truth: a hash ``slots`` mapping ``call_id -> {worker_id, lease_expiry}``.
Capacity = count of non-expired members. Acquire is one atomic Lua script (count non-expired,
reap expired, reject if at cap, else insert this call's slot + lease). A crashed worker leaks
exactly one reclaimable entry that the reconciler (a later step) finds by its expired lease.

The stored ``worker_id`` doubles as the fencing token: renew/release succeed only for the
current owner, so a worker that lost its lease cannot keep writing (it self-aborts).

``now`` is passed from the client (not Redis TIME) to keep the scripts deterministic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.commands.core import AsyncScript

_ACQUIRE_LUA = """
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local cap = tonumber(ARGV[5])
local slots = redis.call('HGETALL', KEYS[1])
local active = 0
local mine = false
for i = 1, #slots, 2 do
  local field = slots[i]
  local ok, v = pcall(cjson.decode, slots[i + 1])
  if ok and v.lease_expiry and tonumber(v.lease_expiry) > now then
    if field == ARGV[1] then mine = true else active = active + 1 end
  else
    redis.call('HDEL', KEYS[1], field)
  end
end
if (not mine) and active >= cap then
  return 0
end
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode({worker_id = ARGV[2], lease_expiry = now + ttl}))
return 1
"""

_RENEW_LUA = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return 0 end
local ok, v = pcall(cjson.decode, raw)
if (not ok) or v.worker_id ~= ARGV[2] then return 0 end
v.lease_expiry = tonumber(ARGV[3]) + tonumber(ARGV[4])
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(v))
return 1
"""

_RELEASE_LUA = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return 0 end
local ok, v = pcall(cjson.decode, raw)
if (not ok) or v.worker_id ~= ARGV[2] then return 0 end
redis.call('HDEL', KEYS[1], ARGV[1])
return 1
"""


@dataclass(slots=True)
class AdmissionResult:
    admitted: bool
    active_count: int


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

    async def acquire(self, call_id: str) -> bool:
        """Atomically admit ``call_id`` if under cap (idempotent renew if already owned)."""
        res = await self._acquire(
            keys=[self._key],
            args=[call_id, self._worker_id, _now_ms(), self._lease_ttl_ms, self._cap],
        )
        return bool(res)

    async def renew(self, call_id: str) -> bool:
        """Extend the lease. Returns False if ownership was lost (caller must self-abort)."""
        res = await self._renew(
            keys=[self._key],
            args=[call_id, self._worker_id, _now_ms(), self._lease_ttl_ms],
        )
        return bool(res)

    async def release(self, call_id: str) -> bool:
        """Release the slot; only the owning worker can (fencing)."""
        res = await self._release(keys=[self._key], args=[call_id, self._worker_id])
        return bool(res)

    async def active_count(self) -> int:
        """Non-expired slot count (read-only; does not reap)."""
        import json

        now = _now_ms()
        raw = await self._redis.hgetall(self._key)
        count = 0
        for value in raw.values():
            try:
                v = json.loads(value)
            except (ValueError, TypeError):
                continue
            if v.get("lease_expiry", 0) > now:
                count += 1
        return count
