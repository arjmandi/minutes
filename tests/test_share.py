"""Public share links (spec v3 §18): owner enables/rotates/disables; anyone with the opaque token
reads anonymously; rotation revokes old URLs; management is owner-scoped. DB + Redis required.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.audio.frames import encode_frame
from app.auth.passwords import hash_password
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


def _login(c: TestClient, email: str) -> None:
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200


def _capture(c: TestClient, email: str, ext: str, frames: int = 2) -> str:
    dt = c.post(
        "/api/auth/login", json={"email": email, "password": PW, "client": "device"}
    ).json()["device_token"]
    token = c.post(
        "/api/capture/token",
        headers={"Authorization": "Bearer " + dt},
        json={"platform": "meet", "external_meeting_id": ext},
    ).json()["token"]
    with c.websocket_connect(f"/ingest?token={token}") as ws:
        ws.send_json(
            {"type": "hello", "platform": "meet", "external_meeting_id": ext,
             "call_id": f"rc-{uuid.uuid4().hex[:8]}"}
        )
        assert ws.receive_json()["type"] == "admitted"
        for i in range(frames):
            ws.send_bytes(encode_frame(i, i * 20, b"\x00\x00" * 160))
        ws.send_json({"type": "end"})
        assert ws.receive_json()["type"] == "ended"
    return next(m for m in c.get("/api/meetings").json() if m["external_meeting_id"] == ext)["id"]


def test_share_anonymous_read_rotate_disable():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"sh-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        _login(c, email)
        ext = f"sh-mtg-{uuid.uuid4().hex[:8]}"
        mid = _capture(c, email, ext, frames=2)

        r = c.post(f"/api/meetings/{mid}/share")
        assert r.status_code == 200 and r.json()["share"]["enabled"] is True
        token = r.json()["share"]["token"]
        assert token

        # Anonymous (no session cookie) read works and leaks no identity fields.
        c.cookies.clear()
        meta = c.get(f"/api/shared/{token}")
        assert meta.status_code == 200
        body = meta.json()
        assert "owner_id" not in body and "external_meeting_id" not in body
        assert c.get(f"/api/shared/{token}/transcript").json().__len__() == 2
        assert c.get(f"/api/shared/{token}/export?format=txt").status_code == 200
        assert c.get(f"/api/shared/{'x' * 40}").status_code == 404  # unknown token

        # The anonymous export must NOT leak the external meeting id, the share token, or private
        # translation config (json meeting block is the curated public view).
        je = c.get(f"/api/shared/{token}/export?format=json").json()
        blob = json.dumps(je)
        assert ext not in blob and token not in blob
        assert "external_meeting_id" not in je["meeting"]
        assert "prompt" not in je["meeting"]["translation"]
        assert ext not in c.get(f"/api/shared/{token}/export?format=md").text

        # Rotate -> old URL dies, new works.
        _login(c, email)
        new_token = c.post(f"/api/meetings/{mid}/share", json={"rotate": True}).json()["share"][
            "token"
        ]
        assert new_token and new_token != token
        c.cookies.clear()
        assert c.get(f"/api/shared/{token}").status_code == 404
        assert c.get(f"/api/shared/{new_token}").status_code == 200

        # Disable -> link stops resolving.
        _login(c, email)
        assert c.delete(f"/api/meetings/{mid}/share").json()["share"]["enabled"] is False
        c.cookies.clear()
        assert c.get(f"/api/shared/{new_token}").status_code == 404


def test_share_management_is_owner_scoped():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        owner = f"so-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(owner)
        _login(c, owner)
        mid = _capture(c, owner, f"so-mtg-{uuid.uuid4().hex[:8]}", frames=1)
        token = c.post(f"/api/meetings/{mid}/share").json()["share"]["token"]

        other = f"sx-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(other)
        c.post("/api/auth/logout")
        _login(c, other)
        assert c.post(f"/api/meetings/{mid}/share").status_code == 404
        assert c.delete(f"/api/meetings/{mid}/share").status_code == 404

        # The public link is for everyone, though.
        c.cookies.clear()
        assert c.get(f"/api/shared/{token}").status_code == 200
