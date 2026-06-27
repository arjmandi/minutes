"""Test fixtures."""

from __future__ import annotations

import os

import pytest
import redis as redis_sync


@pytest.fixture(scope="session", autouse=True)
def _clear_admission_slots():
    """Tests share the cap-of-5 admission key; clear stale leases (60s TTL) from prior runs so the
    cap isn't falsely exhausted across rapid re-runs. Best-effort (no-op if Redis is down)."""
    url = os.environ.get("MINUTES_REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis_sync.from_url(url)
        client.delete("minutes:admission:slots")
        client.close()
    except Exception:  # noqa: BLE001
        pass
    yield
