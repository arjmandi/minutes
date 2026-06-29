"""Per-user Soniox region (data residency).

Each user brings their own Soniox key AND its region ("us" | "eu"); live capture and uploads use
the owner's region to pick US vs EU endpoints — the admin never sets a server-wide region. Covers
the region→endpoint helpers, the per-user transcriber factory, and the settings round-trip.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app import crypto
from app.auth.passwords import hash_password
from app.config import get_settings, soniox_file_base, soniox_rt_url
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.db.models import User
from app.main import app
from app.transcribe.resolve import build_user_transcriber_factory

PW = "Sup3r-Secret-Pass!"


def test_region_endpoints():
    assert soniox_rt_url("eu") == "wss://stt-rt.eu.soniox.com/transcribe-websocket"
    assert soniox_rt_url("us") == "wss://stt-rt.soniox.com/transcribe-websocket"
    assert soniox_rt_url(None) == "wss://stt-rt.soniox.com/transcribe-websocket"  # null -> US
    assert soniox_file_base("eu") == "https://api.eu.soniox.com/v1"
    assert soniox_file_base("us") == "https://api.soniox.com/v1"
    assert soniox_file_base(None) == "https://api.soniox.com/v1"


def test_user_transcriber_factory_uses_user_region():
    settings = get_settings()
    uid = uuid.uuid4()
    enc = crypto.encrypt("fake-soniox-key", secret=settings.secret_key, aad=str(uid))
    eu_user = User(
        id=uid, email="x@test.io", password_hash="x", soniox_key_enc=enc, soniox_region="eu"
    )
    factory = build_user_transcriber_factory(eu_user, settings=settings)
    assert factory is not None
    assert factory(None)._url == soniox_rt_url("eu")  # EU endpoint flows through

    # No key -> None (the caller falls back to the server/fake factory).
    nokey = User(id=uuid.uuid4(), email="y@test.io", password_hash="x")
    assert build_user_transcriber_factory(nokey, settings=settings) is None


def test_region_roundtrips_via_keys_endpoint():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"reg-{uuid.uuid4().hex[:8]}@test.io"

        async def _mk() -> None:
            engine = make_engine(get_settings().database_url)
            factory = make_session_factory(engine)
            try:
                async with factory() as db:
                    await repo.create_user(db, email=email, password_hash=hash_password(PW))
                    await db.commit()
            finally:
                await engine.dispose()

        asyncio.run(_mk())
        c.post("/api/auth/login", json={"email": email, "password": PW})

        assert c.get("/api/me").json()["soniox_region"] == "us"  # default
        r = c.put("/api/me/keys", json={"soniox_region": "eu"})
        assert r.status_code == 200 and r.json()["soniox_region"] == "eu"
        assert c.get("/api/me").json()["soniox_region"] == "eu"  # persisted
        # Region is constrained — a bogus value is rejected, not silently stored.
        assert c.put("/api/me/keys", json={"soniox_region": "asia"}).status_code == 422
