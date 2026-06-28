"""Meeting management: rename + export (transcript/translation/both, ±timestamps, txt/md/json).

Exercises the owner-scoped rename and the export endpoint end-to-end over a captured meeting whose
segments carry de translations (owner defaults seed translation; key-less dev fake produces them).
DB + Redis required; skips otherwise.
"""

from __future__ import annotations

import asyncio
import re
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
TS = re.compile(r"\[\d{2}:\d{2}:\d{2}\]")


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


def _device_token(c: TestClient, email: str) -> str:
    return c.post(
        "/api/auth/login", json={"email": email, "password": PW, "client": "device"}
    ).json()["device_token"]


def _capture(c: TestClient, email: str, ext: str, frames: int = 2) -> None:
    token = c.post(
        "/api/capture/token",
        headers={"Authorization": "Bearer " + _device_token(c, email)},
        json={"platform": "meet", "external_meeting_id": ext},
    ).json()["token"]
    with c.websocket_connect(f"/ingest?token={token}") as ws:
        ws.send_json(
            {
                "type": "hello",
                "platform": "meet",
                "external_meeting_id": ext,
                "call_id": f"rc-{uuid.uuid4().hex[:8]}",
            }
        )
        assert ws.receive_json()["type"] == "admitted"
        for i in range(frames):
            ws.send_bytes(encode_frame(i, i * 20, b"\x00\x00" * 160))
        ws.send_json({"type": "end"})
        assert ws.receive_json()["type"] == "ended"


def test_rename_and_export():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"mm-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        c.post("/api/auth/login", json={"email": email, "password": PW})
        c.put(
            "/api/me/settings",
            json={"default_translation_on": True, "default_output_language": "de"},
        )

        ext = f"mm-mtg-{uuid.uuid4().hex[:8]}"
        _capture(c, email, ext, frames=2)
        meetings = c.get("/api/meetings").json()
        mid = next(m for m in meetings if m["external_meeting_id"] == ext)["id"]

        # Rename
        r = c.put(f"/api/meetings/{mid}", json={"title": "Quarterly Review"})
        assert r.status_code == 200 and r.json()["title"] == "Quarterly Review"

        # Transcript surfaces absolute timestamps.
        seg0 = c.get(f"/api/meetings/{mid}/transcript").json()[0]
        assert seg0["started_at"] is not None

        # txt export, both, with timestamps -> has [HH:MM:SS] + the de translation ("[de] ").
        both = c.get(f"/api/meetings/{mid}/export?format=txt&include=both&timestamps=true")
        assert both.status_code == 200
        assert "attachment" in both.headers["content-disposition"]
        assert TS.search(both.text) and "[de]" in both.text

        # timestamps=false drops the clock prefix.
        no_ts = c.get(f"/api/meetings/{mid}/export?format=txt&include=both&timestamps=false").text
        assert not TS.search(no_ts)

        # transcript-only excludes the translation.
        only = c.get(f"/api/meetings/{mid}/export?format=txt&include=transcript").text
        assert "[de]" not in only

        # markdown has the title heading.
        md = c.get(f"/api/meetings/{mid}/export?format=md&include=transcript").text
        assert md.startswith("# Quarterly Review")

        # json export is structured.
        j = c.get(f"/api/meetings/{mid}/export?format=json").json()
        assert j["meeting"]["title"] == "Quarterly Review"
        assert len(j["segments"]) == 2 and "started_at" in j["segments"][0]

        # bad params -> 422; non-owner -> 404.
        assert c.get(f"/api/meetings/{mid}/export?format=pdf").status_code == 422
        other = f"o-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(other)
        c.post("/api/auth/logout")
        c.post("/api/auth/login", json={"email": other, "password": PW})
        assert c.get(f"/api/meetings/{mid}/export").status_code == 404
        assert c.put(f"/api/meetings/{mid}", json={"title": "x"}).status_code == 404
