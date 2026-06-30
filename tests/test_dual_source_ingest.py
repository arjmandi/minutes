"""Dual-source capture, Phase 2: the ingest WS carries `source`, creates a session per source, and
tags the transcript; the owner-binding gate rejects a non-owner principal.

Uses the fake transcriber (empty Soniox key) so capture runs offline. DB + Redis required.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.audio.frames import encode_frame
from app.auth.passwords import hash_password
from app.auth.tokens import issue_capability_token
from app.config import get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.main import app

PW = "Sup3r-Secret-Pass!"


def _make_user(email: str) -> None:
    async def _run() -> None:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as db:
                await repo.create_user(db, email=email, password_hash=hash_password(PW))
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _device_token(client: TestClient, email: str) -> str:
    r = client.post("/api/auth/login", json={"email": email, "password": PW, "client": "device"})
    assert r.status_code == 200
    return r.json()["device_token"]


def _capture_token(client: TestClient, device_token: str, ext: str) -> str:
    r = client.post(
        "/api/capture/token",
        headers={"Authorization": "Bearer " + device_token},
        json={"platform": "meet", "external_meeting_id": ext},
    )
    assert r.status_code == 200
    return r.json()["token"]


def _capture(client: TestClient, token: str, ext: str, *, source: str, call_id: str) -> None:
    with client.websocket_connect(f"/ingest?token={token}") as ws:
        ws.send_json({"type": "hello", "platform": "meet", "external_meeting_id": ext,
                      "call_id": call_id, "source": source})
        admitted = ws.receive_json()
        assert admitted["type"] == "admitted"
        assert admitted["source"] == source  # the admit echoes the source
        for i in range(2):
            ws.send_bytes(encode_frame(i, i * 20, b"\x00\x00" * 160))
        ws.send_json({"type": "end"})
        assert ws.receive_json()["type"] == "ended"


def test_tab_and_mic_capture_tag_the_transcript():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"ds-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        c.post("/api/auth/login", json={"email": email, "password": PW})
        dt = _device_token(c, email)
        ext = f"ds-{uuid.uuid4().hex[:8]}"
        cap = _capture_token(c, dt, ext)  # one token, claims ownership; reused for both sources

        _capture(c, cap, ext, source="tab", call_id=f"tab-{uuid.uuid4().hex[:8]}")
        _capture(c, cap, ext, source="mic", call_id=f"mic-{uuid.uuid4().hex[:8]}")

        meeting = next(m for m in c.get("/api/meetings").json() if m["external_meeting_id"] == ext)
        mid = meeting["id"]
        segs = c.get(f"/api/meetings/{mid}/transcript").json()
        assert segs, "both captures should have produced segments"
        assert {s["source"] for s in segs} == {"tab", "mic"}  # every segment is source-tagged

        mic_only = c.get(f"/api/meetings/{mid}/transcript?source=mic").json()
        assert mic_only and all(s["source"] == "mic" for s in mic_only)

        assert c.get(f"/api/meetings/{mid}/transcript?source=bogus").status_code == 422


def test_owner_binding_rejects_other_principal():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"own-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        c.post("/api/auth/login", json={"email": email, "password": PW})
        dt = _device_token(c, email)
        ext = f"own-{uuid.uuid4().hex[:8]}"
        _capture_token(c, dt, ext)  # claims the meeting for this user (owner = user.id)

        s = get_settings()
        intruder = issue_capability_token(
            principal=str(uuid.uuid4()), secret=s.auth_secret, algorithm=s.auth_algorithm,
            ttl_s=60, meetings=[f"meet:{ext}"],
        )
        with c.websocket_connect(f"/ingest?token={intruder}") as ws:
            ws.send_json({"type": "hello", "platform": "meet", "external_meeting_id": ext,
                          "call_id": f"x-{uuid.uuid4().hex[:8]}", "source": "mic"})
            msg = ws.receive_json()
            assert msg["type"] == "forbidden" and msg["reason"] == "not_owner"
