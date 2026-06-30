"""Dual-source capture, Phase 1: `source` on Session + the read/repo plumbing.

Covers: two sources (tab + mic) coexist for one meeting; distinct_sources; the
per-(meeting, source) live invariant (a 2nd live session of the same source -> SourceConflict via
the partial unique index); same-call_id reconnect with a different source is refused; same-call_id
same-source reconnect takes over; and transcript_for_meeting tags + filters by source. DB required.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest

from app.config import get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.db.models import CaptureSource, SessionStatus


def _run(body: Callable[[object], Awaitable[None]]) -> None:
    async def _outer() -> None:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as db:
                await body(db)
        finally:
            await engine.dispose()

    asyncio.run(_outer())


async def _meeting(db, platform: str = "web"):
    ext = f"ds-{uuid.uuid4().hex}"
    m = await repo.upsert_meeting(db, platform=platform, external_meeting_id=ext)
    await db.commit()
    return m


async def _sess(db, m, source, *, run="r", cid=None):
    return await repo.create_session(
        db, meeting_id=m.id, platform_call_id=cid or uuid.uuid4().hex, run_id=run, source=source
    )


def test_two_sources_coexist():
    async def body(db):
        m = await _meeting(db)
        await _sess(db, m, CaptureSource.tab)
        await _sess(db, m, CaptureSource.mic)
        await db.commit()
        assert set(await repo.distinct_sources(db, m.id)) == {CaptureSource.tab, CaptureSource.mic}

    _run(body)


def test_second_live_session_same_source_conflicts():
    async def body(db):
        m = await _meeting(db)
        await _sess(db, m, CaptureSource.mic, run="r1")
        await db.commit()
        with pytest.raises(repo.SourceConflict):
            await _sess(db, m, CaptureSource.mic, run="r2")  # a different call_id, same live source

    _run(body)


def test_same_call_id_different_source_refused():
    async def body(db):
        m = await _meeting(db)
        cid = uuid.uuid4().hex
        await _sess(db, m, CaptureSource.tab, run="r1", cid=cid)
        await db.commit()
        with pytest.raises(repo.SourceConflict):
            await _sess(db, m, CaptureSource.mic, run="r2", cid=cid)  # call_id can't switch source

    _run(body)


def test_same_call_id_same_source_takes_over():
    async def body(db):
        m = await _meeting(db)
        cid = uuid.uuid4().hex
        s1 = await _sess(db, m, CaptureSource.tab, run="r1", cid=cid)
        await db.commit()
        await repo.mark_session(db, session_id=s1.id, status=SessionStatus.ended)
        await db.commit()
        s2 = await _sess(db, m, CaptureSource.tab, run="r2", cid=cid)
        await db.commit()
        assert s2.id == s1.id and s2.run_id == "r2" and s2.status == SessionStatus.active

    _run(body)


def test_transcript_tags_and_filters_by_source():
    async def body(db):
        m = await _meeting(db)
        tab = await _sess(db, m, CaptureSource.tab)
        mic = await _sess(db, m, CaptureSource.mic)
        await repo.upsert_segment(db, meeting_id=m.id, session_id=tab.id, speaker_id="mixed",
                                  utterance_id="u1", text="they said", language="en",
                                  start_ms=0, end_ms=20)
        await repo.upsert_segment(db, meeting_id=m.id, session_id=mic.id, speaker_id="mixed",
                                  utterance_id="u1", text="i said", language="en",
                                  start_ms=0, end_ms=20)
        await db.commit()

        all_rows = await repo.transcript_for_meeting(db, m.id)
        assert {src: seg.text for seg, _j, src in all_rows} == {
            CaptureSource.tab: "they said", CaptureSource.mic: "i said"}

        mic_only = await repo.transcript_for_meeting(db, m.id, source=CaptureSource.mic)
        assert [seg.text for seg, _j, _s in mic_only] == ["i said"]

    _run(body)


def test_upload_source_tag():
    async def body(db):
        m = await _meeting(db, platform="upload")
        s = await _sess(db, m, CaptureSource.upload, cid=f"upload:{uuid.uuid4()}")
        await db.commit()
        assert s.source == CaptureSource.upload
        assert await repo.distinct_sources(db, m.id) == [CaptureSource.upload]

    _run(body)
