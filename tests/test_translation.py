"""First-class per-meeting translation (spec v3 §7).

Covers config seeding from the owner's defaults on capture-token claim, live auto-translate during
capture (owner has no Anthropic key -> dev fake fallback), the per-meeting config endpoint, and the
on-demand "translate this line" endpoint (422 without a provider key). DB + Redis required.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audio.frames import encode_frame
from app.auth.passwords import hash_password
from app.config import get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.db.models import Translation, TranslationSource, TranslationStatus
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
    r = client.post(
        "/api/auth/login", json={"email": email, "password": PW, "client": "device"}
    )
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


def _capture(client: TestClient, token: str, ext: str, frames: int = 2) -> None:
    with client.websocket_connect(f"/ingest?token={token}") as ws:
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


def test_translation_seeded_from_defaults_and_auto_translated():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"tr-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        c.post("/api/auth/login", json={"email": email, "password": PW})
        # Owner defaults: translate ON -> de. These seed the meeting on capture-token claim.
        c.put(
            "/api/me/settings",
            json={"default_translation_on": True, "default_output_language": "de"},
        )

        ext = f"tr-mtg-{uuid.uuid4().hex[:8]}"
        cap = _capture_token(c, _device_token(c, email), ext)
        _capture(c, cap, ext, frames=2)  # session auto-translates to de (no key -> dev fake)

        meeting = next(
            m for m in c.get("/api/meetings").json() if m["external_meeting_id"] == ext
        )
        assert meeting["translation"]["enabled"] is True
        assert meeting["translation"]["output_language"] == "de"

        segs = c.get(f"/api/meetings/{meeting['id']}/transcript").json()
        assert segs, "capture should have produced final segments"
        for s in segs:
            de = [t for t in s["translations"] if t["target_language"] == "de"]
            assert de and de[0]["status"] == "ok" and de[0]["source"] == "auto"


def test_config_endpoint_and_on_demand_requires_key():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"tc-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        c.post("/api/auth/login", json={"email": email, "password": PW})

        ext = f"tc-mtg-{uuid.uuid4().hex[:8]}"
        cap = _capture_token(c, _device_token(c, email), ext)  # no defaults -> nothing seeded
        _capture(c, cap, ext, frames=2)

        meeting = next(
            m for m in c.get("/api/meetings").json() if m["external_meeting_id"] == ext
        )
        mid = meeting["id"]
        assert meeting["translation"]["enabled"] is False  # not seeded (user had no defaults)

        # Can't enable without a target language.
        assert c.put(f"/api/meetings/{mid}/translation", json={"enabled": True}).status_code == 422

        r = c.put(
            f"/api/meetings/{mid}/translation",
            json={"enabled": True, "output_language": "fa", "prompt": "Formal tone."},
        )
        assert r.status_code == 200
        assert r.json()["translation"] == {
            "enabled": True,
            "output_language": "fa",
            "input_language": "detect",
            "prompt": "Formal tone.",
            "model": None,
        }

        # On-demand translate: owner has no Anthropic key -> 422 (no provider).
        seg = c.get(f"/api/meetings/{mid}/transcript").json()[0]
        od = c.post(f"/api/meetings/{mid}/segments/{seg['id']}/translate")
        assert od.status_code == 422

        # A non-owner cannot read or configure (owner-scoped -> 404).
        other = f"o-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(other)
        c.post("/api/auth/logout")
        c.post("/api/auth/login", json={"email": other, "password": PW})
        assert c.put(f"/api/meetings/{mid}/translation", json={"enabled": False}).status_code == 404


def test_failed_translation_never_clobbers_a_good_one():
    """Regression: a failed/empty retry must not erase a prior ok translation; a later success
    (manual or auto) does win and updates provenance."""
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")

    async def _run() -> None:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as db:
                meeting = await repo.upsert_meeting(
                    db, platform="meet", external_meeting_id=f"clob-{uuid.uuid4().hex[:8]}"
                )
                session = await repo.create_session(
                    db,
                    meeting_id=meeting.id,
                    platform_call_id=f"cb-{uuid.uuid4().hex[:8]}",
                    run_id="r1",
                )
                seg_id = await repo.upsert_segment(
                    db,
                    meeting_id=meeting.id,
                    session_id=session.id,
                    speaker_id="mixed",
                    utterance_id="u1",
                    text="hallo welt",
                    language="en",
                    start_ms=0,
                    end_ms=20,
                )
                await repo.upsert_translation(
                    db, segment_id=seg_id, target_language="de", text="gut",
                    status=TranslationStatus.ok, source=TranslationSource.auto,
                )
                await repo.upsert_translation(  # failed retry — must be ignored
                    db, segment_id=seg_id, target_language="de", text="",
                    status=TranslationStatus.failed, source=TranslationSource.auto,
                )
                await db.commit()
                db.expire_all()  # Core UPDATE bypasses the ORM map; force a fresh read
                row = (
                    await db.execute(select(Translation).where(Translation.segment_id == seg_id))
                ).scalar_one()
                assert row.text == "gut" and row.status == TranslationStatus.ok

                await repo.upsert_translation(  # successful manual re-translate — wins
                    db, segment_id=seg_id, target_language="de", text="besser",
                    status=TranslationStatus.ok, source=TranslationSource.manual,
                )
                await db.commit()
                db.expire_all()
                row = (
                    await db.execute(select(Translation).where(Translation.segment_id == seg_id))
                ).scalar_one()
                assert row.text == "besser" and row.source == TranslationSource.manual
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_web_capture_token_creates_named_web_meeting():
    """Generic 'web' tab capture: /capture/token accepts platform=web + a title and creates a
    web-platform meeting named after the tab."""
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"web-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        c.post("/api/auth/login", json={"email": email, "password": PW})
        dt = _device_token(c, email)
        ext = f"web-{uuid.uuid4().hex}"
        r = c.post(
            "/api/capture/token",
            headers={"Authorization": "Bearer " + dt},
            json={"platform": "web", "external_meeting_id": ext, "title": "My YouTube Video"},
        )
        assert r.status_code == 200 and r.json()["scope"] == f"web:{ext}"
        m = next(x for x in c.get("/api/meetings").json() if x["external_meeting_id"] == ext)
        assert m["platform"] == "web" and m["title"] == "My YouTube Video"
