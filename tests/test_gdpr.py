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
from app.auth.passwords import hash_password
from app.auth.tokens import issue_capability_token
from app.config import Settings, get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.db.models import AudioChunk, ChunkState, Meeting, Platform, Session, SessionStatus
from app.jobs import reconcile, retention
from app.main import app, create_app

PW = "Sup3r-Secret-Pass!"


def _token() -> str:
    s = get_settings()
    return issue_capability_token(
        principal="op", secret=s.auth_secret, algorithm=s.auth_algorithm, ttl_s=60, meetings=["*"]
    )


def _token_for(principal: str) -> str:
    """A capability token bound to a specific principal (for the owner-binding ingest gate)."""
    s = get_settings()
    return issue_capability_token(
        principal=principal, secret=s.auth_secret, algorithm=s.auth_algorithm,
        ttl_s=60, meetings=["*"],
    )


def _user_id(email: str) -> str:
    async def _run() -> str:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as db:
                user = await repo.get_user_by_email(db, email)
                return str(user.id)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _make_user(email: str, *, admin: bool) -> None:
    async def _run() -> None:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as db:
                await repo.create_user(
                    db, email=email, password_hash=hash_password(PW), is_admin=admin
                )
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _login(client: TestClient, email: str) -> None:
    assert client.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200


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
        _capture(client, ext)  # creates an (unowned) meeting
        admin_email = f"adm-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(admin_email, admin=True)
        _login(client, admin_email)
        meetings = client.get("/api/meetings").json()
        meeting = next(m for m in meetings if m["external_meeting_id"] == ext)
        # A non-admin (non-owner) cannot erase it — owner-scoped 404.
        client.post("/api/auth/logout")
        other_email = f"oth-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(other_email, admin=False)
        _login(client, other_email)
        assert client.delete(f"/api/meetings/{meeting['id']}").status_code == 404
        # Admin erases.
        client.post("/api/auth/logout")
        _login(client, admin_email)
        resp = client.delete(f"/api/meetings/{meeting['id']}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # transcript gone (404)
        assert client.get(f"/api/meetings/{meeting['id']}/transcript").status_code == 404


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
        # Grant consent (as a signed-in user).
        email = f"cg-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email, admin=False)
        _login(client, email)
        granted = client.post(
            "/api/meetings/consent",
            json={"platform": "meet", "external_meeting_id": ext, "status": "granted"},
        )
        assert granted.status_code == 200
        assert granted.json()["consent_status"] == "granted"
        # Now admitted — capture must be by the meeting OWNER (granting consent claimed it), so use
        # a token bound to that user's principal (the owner-binding ingest gate rejects others).
        owner_token = _token_for(_user_id(email))
        with client.websocket_connect(f"/ingest?token={owner_token}") as ws:
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
