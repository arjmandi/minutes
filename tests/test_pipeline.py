"""End-to-end ingest pipeline test (FakeTranscriber): frames in -> segments persisted.

Requires Postgres + Redis up (and no MINUTES_SONIOX_API_KEY, so the fake is used). Skips
otherwise. The FakeTranscriber emits one final segment per audio chunk, so N frames -> N rows.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.audio.frames import encode_frame
from app.auth.tokens import issue_capability_token
from app.config import get_settings
from app.db.base import make_engine, make_session_factory
from app.db.models import AudioChunk, ChunkState, TranscriptSegment, Translation
from app.main import app


def _token() -> str:
    settings = get_settings()
    return issue_capability_token(
        principal="op",
        secret=settings.auth_secret,
        algorithm=settings.auth_algorithm,
        ttl_s=60,
        meetings=["*"],
    )


def _counts(session_id: str) -> tuple[int, int]:
    async def _q() -> tuple[int, int]:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        async with factory() as db:
            segs = await db.scalar(
                select(func.count())
                .select_from(TranscriptSegment)
                .where(TranscriptSegment.session_id == uuid.UUID(session_id))
            )
            trans = await db.scalar(
                select(func.count())
                .select_from(Translation)
                .join(TranscriptSegment, Translation.segment_id == TranscriptSegment.id)
                .where(TranscriptSegment.session_id == uuid.UUID(session_id))
            )
        await engine.dispose()
        return int(segs or 0), int(trans or 0)

    return asyncio.run(_q())


def _chunk_counts(session_id: str) -> tuple[int, int]:
    async def _q() -> tuple[int, int]:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        async with factory() as db:
            total = await db.scalar(
                select(func.count())
                .select_from(AudioChunk)
                .where(AudioChunk.session_id == uuid.UUID(session_id))
            )
            recorded = await db.scalar(
                select(func.count())
                .select_from(AudioChunk)
                .where(
                    AudioChunk.session_id == uuid.UUID(session_id),
                    AudioChunk.state == ChunkState.recorded,
                )
            )
        await engine.dispose()
        return int(total or 0), int(recorded or 0)

    return asyncio.run(_q())


def test_pipeline_persists_one_segment_per_frame():
    settings = get_settings()
    if settings.soniox_api_key:
        pytest.skip("real Soniox key set; this test asserts FakeTranscriber behavior")
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        call_id = f"pipe-{uuid.uuid4().hex[:8]}"
        with client.websocket_connect(f"/ingest?token={_token()}") as ws:
            ws.send_json(
                {
                    "type": "hello",
                    "platform": "meet",
                    "external_meeting_id": f"pipe-mtg-{uuid.uuid4().hex[:8]}",
                    "call_id": call_id,
                }
            )
            assert ws.receive_json()["type"] == "admitted"
            for i in range(3):
                ws.send_bytes(encode_frame(i, i * 20, b"\x00\x00" * 160))
            ws.send_json({"type": "end"})
            ack = ws.receive_json()
            assert ack["type"] == "ended"
            assert ack["segments"] == 3
            session_id = ack["session_id"]

    segs, trans = _counts(session_id)
    assert segs == 3
    # FakeTranscriber emits source language "en"; FakeTranslator translates each segment into
    # each non-"en" target. So translations == segments × (non-"en" targets).
    non_en_targets = [t for t in get_settings().translation_targets if t != "en"]
    assert trans == 3 * len(non_en_targets)

    # Audio archival: the final partial chunk is flushed on end and marked RECORDED (FakeStorage).
    total_chunks, recorded_chunks = _chunk_counts(session_id)
    assert total_chunks == 1
    assert recorded_chunks == 1
