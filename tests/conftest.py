"""Test fixtures."""

from __future__ import annotations

import os

import pytest
import redis as redis_sync

# The suite is written against the deterministic fakes (FakeTranscriber emits one segment per
# frame, FakeTranslator and FakeStorage need no network). Provider selection is configuration
# read through ``.env``, and a developer's ``.env`` usually carries real keys, so without this
# override the end-to-end tests would stream silent test frames to Soniox and count fewer
# segments than the fakes produce. Environment variables take precedence over ``.env``, and
# this module is imported before any test module imports ``app.main``.
for _name in ("MINUTES_SONIOX_API_KEY", "MINUTES_ANTHROPIC_API_KEY"):
    os.environ[_name] = ""
os.environ["MINUTES_S3_ENABLED"] = "false"


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
