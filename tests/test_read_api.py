"""Read API tests: transcript retrieval, owner-scoped authorization, translations.

Capture drives the ingest pipeline (capability token, unchanged); the read API is now owner-scoped
to the signed-in web user (admin sees all). Requires Postgres + Redis; skips otherwise.
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


def _cap_token(meetings: list[str]) -> str:
    s = get_settings()
    return issue_capability_token(
        principal="op", secret=s.auth_secret, algorithm=s.auth_algorithm,
        ttl_s=60, meetings=meetings,
    )


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


def _capture(client: TestClient, token: str, external_meeting_id: str, frames: int = 2) -> None:
    with client.websocket_connect(f"/ingest?token={token}") as ws:
        ws.send_json(
            {
                "type": "hello",
                "platform": "meet",
                "external_meeting_id": external_meeting_id,
                "call_id": f"rc-{uuid.uuid4().hex[:8]}",
            }
        )
        assert ws.receive_json()["type"] == "admitted"
        for i in range(frames):
            ws.send_bytes(encode_frame(i, i * 20, b"\x00\x00" * 160))
        ws.send_json({"type": "end"})
        assert ws.receive_json()["type"] == "ended"


def test_transcript_read_authz_and_translations():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        ext = f"read-mtg-{uuid.uuid4().hex[:8]}"
        _capture(client, _cap_token(["*"]), ext, frames=2)  # creates an (unowned) meeting

        # Admin (session cookie) sees + reads any meeting.
        admin_email = f"admin-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(admin_email, admin=True)
        _login(client, admin_email)
        meetings = client.get("/api/meetings").json()
        meeting = next(m for m in meetings if m["external_meeting_id"] == ext)

        resp = client.get(f"/api/meetings/{meeting['id']}/transcript")
        assert resp.status_code == 200
        segs = resp.json()
        assert len(segs) == 2
        assert [s["meeting_seq"] for s in segs] == sorted(s["meeting_seq"] for s in segs)

        # Translation is now first-class per meeting (not server-wide targets): an unowned,
        # capture-created meeting has translation disabled, so it carries no translations.
        assert all(s["translations"] == [] for s in segs)

        # A non-admin user neither sees nor can read someone else's (here: unowned) meeting.
        client.post("/api/auth/logout")
        other_email = f"other-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(other_email, admin=False)
        _login(client, other_email)
        forbidden = client.get(f"/api/meetings/{meeting['id']}/transcript")
        assert forbidden.status_code == 404  # owner-scoped -> 404, no existence leak
        listed = client.get("/api/meetings").json()
        assert all(m["external_meeting_id"] != ext for m in listed)
