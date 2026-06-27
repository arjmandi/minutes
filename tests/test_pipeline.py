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
from app.db.models import TranscriptSegment
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


def _count_segments(session_id: str) -> int:
    async def _q() -> int:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        async with factory() as db:
            n = await db.scalar(
                select(func.count())
                .select_from(TranscriptSegment)
                .where(TranscriptSegment.session_id == uuid.UUID(session_id))
            )
        await engine.dispose()
        return int(n or 0)

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

    assert _count_segments(session_id) == 3
