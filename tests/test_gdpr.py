"""GDPR tests (spec v3 §15): erasure, the consent gate, and the retention job.

Require Postgres + Redis; skip otherwise. Use the fake transcriber/translator/storage.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.audio.frames import encode_frame
from app.auth.tokens import issue_capability_token
from app.config import Settings, get_settings
from app.db.base import make_engine, make_session_factory
from app.db.models import AudioChunk, ChunkState, Meeting, Platform, Session, SessionStatus
from app.jobs import reconcile, retention
from app.main import app, create_app


def _token() -> str:
    s = get_settings()
    return issue_capability_token(
        principal="op", secret=s.auth_secret, algorithm=s.auth_algorithm, ttl_s=60, meetings=["*"]
    )


def _bearer() -> dict:
    return {"Authorization": "Bearer " + _token()}


def _admin_bearer() -> dict:
    s = get_settings()
    token = issue_capability_token(
        principal="admin",
        secret=s.auth_secret,
        algorithm=s.auth_algorithm,
        ttl_s=60,
        meetings=["*"],
        admin=True,
    )
    return {"Authorization": "Bearer " + token}


def _hello(ext: str, call_id: str) -> dict:
    return {"type": "hello", "platform": "meet", "external_meeting_id": ext, "call_id": call_id}


def _capture(client: TestClient, ext: str) -> None:
    with client.websocket_connect(f"/ingest?token={_token()}") as ws:
        ws.send_json(_hello(ext, f"c-{uuid.uuid4().hex[:8]}"))
        assert ws.receive_json()["type"] == "admitted"
        for i in range(2):
            ws.send_bytes(encode_frame(i, i * 20, b"\x00\x00" * 160))
        ws.send_json({"type": "end"})
        assert ws.receive_json()["type"] == "ended"


def test_erasure_removes_meeting_and_objects():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        ext = f"erase-{uuid.uuid4().hex[:8]}"
        _capture(client, ext)
        meetings = client.get("/api/meetings", headers=_bearer()).json()
        meeting = next(m for m in meetings if m["external_meeting_id"] == ext)
        # A capture-scoped token cannot erase (admin scope required).
        assert client.delete(f"/api/meetings/{meeting['id']}", headers=_bearer()).status_code == 403
        resp = client.delete(f"/api/meetings/{meeting['id']}", headers=_admin_bearer())
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # transcript gone (404)
        gone = client.get(f"/api/meetings/{meeting['id']}/transcript", headers=_bearer())
        assert gone.status_code == 404


def test_consent_gate_blocks_then_allows():
    consent_app = create_app(Settings(_env_file=None, app_env="test", require_consent=True))
    with TestClient(consent_app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        ext = f"consent-{uuid.uuid4().hex[:8]}"
        # Without consent: refused.
        with client.websocket_connect(f"/ingest?token={_token()}") as ws:
            ws.send_json(_hello(ext, "c1"))
            msg = ws.receive_json()
            assert msg["type"] == "forbidden"
            assert msg["reason"] == "consent_required"
        # Grant consent.
        granted = client.post(
            "/api/meetings/consent",
            headers=_bearer(),
            json={"platform": "meet", "external_meeting_id": ext, "status": "granted"},
        )
        assert granted.status_code == 200
        assert granted.json()["consent_status"] == "granted"
        # Now admitted.
        with client.websocket_connect(f"/ingest?token={_token()}") as ws:
            ws.send_json(_hello(ext, "c2"))
            assert ws.receive_json()["type"] == "admitted"


def test_retention_deletes_expired():
    async def _run() -> tuple[int, object]:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        old_age = timedelta(days=get_settings().retention_days + 5)
        try:
            async with factory() as db:
                old = Meeting(
                    platform=Platform.meet,
                    external_meeting_id=f"ret-old-{uuid.uuid4().hex[:8]}",
                    created_at=datetime.now(UTC) - old_age,
                )
                db.add(old)
                await db.commit()
                old_id = old.id
        except Exception:  # noqa: BLE001 — DB not available
            await engine.dispose()
            return -1, "skip"
        deleted = await retention.run()
        async with factory() as db:
            still = await db.get(Meeting, old_id)
        await engine.dispose()
        return deleted, still

    deleted, still = asyncio.run(_run())
    if still == "skip":
        pytest.skip("datastores not ready")
    assert still is None  # the backdated meeting was purged
    assert deleted >= 1


def test_reconcile_marks_orphan_pending_chunk_lost():
    async def _run() -> object:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as db:
                meeting = Meeting(
                    platform=Platform.meet, external_meeting_id=f"rec-{uuid.uuid4().hex[:8]}"
                )
                db.add(meeting)
                await db.flush()
                session = Session(
                    meeting_id=meeting.id,
                    platform_call_id=f"rc-{uuid.uuid4().hex[:8]}",
                    status=SessionStatus.ended,
                    run_id="w",
                )
                db.add(session)
                await db.flush()
                chunk = AudioChunk(
                    session_id=session.id,
                    speaker_id="mixed",
                    s3_key=f"orphan-{uuid.uuid4().hex[:8]}.wav",
                    seq=0,
                    state=ChunkState.pending,
                    duration_s=1.0,
                )
                db.add(chunk)
                await db.commit()
                chunk_id = chunk.id
        except Exception:  # noqa: BLE001 — DB not available
            await engine.dispose()
            return "skip"
        await reconcile.run()
        async with factory() as db:
            refreshed = await db.get(AudioChunk, chunk_id)
            state = refreshed.state
        await engine.dispose()
        return state

    state = asyncio.run(_run())
    if state == "skip":
        pytest.skip("datastores not ready")
    assert state == ChunkState.lost  # orphan PENDING with no object -> LOST
