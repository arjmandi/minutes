"""Silence-suspend timestamp offset (Session Manager).

When the capture client drops silence (silence-suspend), a frame's real ts_ms jumps ahead of the
audio actually streamed to the transcriber. The manager re-adds that dropped duration to each
segment's start/end so timestamps still track real meeting time. The FakeTranscriber emits one
segment per frame at start_ms=(index-1)*1000, so the offset is directly observable.

Requires Postgres + Redis; skips otherwise (same as the control-plane tests).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from redis.asyncio import Redis

from app.config import Settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.session.events import AudioFrame, EndReason, SessionEnded, SessionStarted
from app.session.manager import SessionManager
from app.storage.factory import make_storage
from app.transcribe.factory import make_transcriber_factory
from app.translate.factory import make_translator_factory

_FRAME = b"\x00\x00" * 1600  # 100 ms of silence @ 16 kHz s16le (3200 bytes)


class _GapAdapter:
    """Emits SessionStarted, one AudioFrame per supplied timestamp, then SessionEnded."""

    def __init__(self, platform: str, ext: str, call_id: str, timestamps: list[float]) -> None:
        self._platform, self._ext, self._call_id, self._ts = platform, ext, call_id, timestamps

    @property
    def call_id(self) -> str:
        return self._call_id

    async def events(self):
        yield SessionStarted(self._platform, self._ext, self._call_id)
        for ts in self._ts:
            yield AudioFrame(pcm=_FRAME, timestamp=ts)
        yield SessionEnded(EndReason.normal)


async def _run(timestamps: list[float]):
    settings = Settings(_env_file=None, app_env="test", soniox_api_key="", anthropic_api_key="")
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.ping()
        async with factory() as db:
            await repo.list_recent_meetings(db, limit=1)
    except Exception:  # noqa: BLE001 — datastores not available
        await redis.aclose()
        await engine.dispose()
        return "skip"
    manager = SessionManager(
        session_factory=factory,
        redis=redis,
        transcriber_factory=make_transcriber_factory(settings),
        translator_factory=make_translator_factory(settings),
        translation_targets=[],
        storage=make_storage(settings),
        chunk_interval_s=300,
        worker_id="w-test",
    )
    ext = f"gap-{uuid.uuid4().hex[:8]}"
    call_id = f"gap-{uuid.uuid4().hex[:8]}"
    try:
        await manager.run(_GapAdapter("meet", ext, call_id, timestamps))
        async with factory() as db:
            session = await repo.get_session_by_call_id(db, call_id)
            rows = await repo.transcript_for_meeting(db, session.meeting_id)
        return sorted(seg.start_ts for seg, *_ in rows)
    finally:
        await redis.aclose()
        await engine.dispose()


def test_silence_offset_shifts_post_gap_segments():
    # Frames at 0.0 / 0.1 s (continuous), then 5.0 / 5.1 s — a 4.8 s gap where the client dropped
    # silence (only 0.2 s of audio was actually streamed before the jump).
    starts = asyncio.run(_run([0.0, 0.1, 5.0, 5.1]))
    if starts == "skip":
        pytest.skip("requires Postgres + Redis")
    # Fake start_ms by frame index = 0,1000,2000,3000 ms; the 4800 ms gap is added to the last two.
    # Persisted as start_ts in SECONDS, so: 0, 1, 6.8, 7.8.
    assert starts == [0.0, 1.0, 6.8, 7.8]


def test_no_gap_leaves_timestamps_unchanged():
    starts = asyncio.run(_run([0.0, 0.1, 0.2, 0.3]))
    if starts == "skip":
        pytest.skip("requires Postgres + Redis")
    assert starts == [0.0, 1.0, 2.0, 3.0]  # start_ts in seconds; no offset added
