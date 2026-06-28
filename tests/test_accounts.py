"""Chunk 2 auth tests: crypto at rest, password policy, web login/me/settings/keys/password/logout,
and device login -> capture token. DB-backed tests skip if datastores are down.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient

from app import crypto
from app.auth.passwords import WeakPassword, hash_password, validate_password, verify_password
from app.config import get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.main import app

GOOD_PW = "Sup3r-Secret-Pass!"  # 12+ chars, 4 character classes


def _ready(client: TestClient) -> bool:
    return client.get("/readyz").status_code == 200


def _make_user(email: str, *, password: str = GOOD_PW, admin: bool = False) -> uuid.UUID:
    async def _run() -> uuid.UUID:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as db:
                user = await repo.create_user(
                    db, email=email, password_hash=hash_password(password), is_admin=admin
                )
                await db.commit()
                return user.id
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_crypto_roundtrip_and_aad_binding():
    secret = "unit-test-secret-key-any-length"
    ct = crypto.encrypt("sk-soniox-123", secret=secret, aad="user-1")
    assert ct != "sk-soniox-123"
    assert crypto.decrypt(ct, secret=secret, aad="user-1") == "sk-soniox-123"
    with pytest.raises(InvalidTag):  # AAD mismatch (different user) must fail to decrypt
        crypto.decrypt(ct, secret=secret, aad="user-2")
    with pytest.raises(InvalidTag):  # wrong key must fail
        crypto.decrypt(ct, secret="different-secret", aad="user-1")


def test_password_policy_and_hashing():
    for weak in ["short", "alllowercase12", "NOLOWER123!"]:  # too short, or < 3 classes
        with pytest.raises(WeakPassword):
            validate_password(weak)
    validate_password(GOOD_PW)
    h = hash_password(GOOD_PW)
    assert verify_password(h, GOOD_PW)
    assert not verify_password(h, "wrong-password")


def test_web_login_me_settings_keys_password_logout():
    with TestClient(app) as c:
        if not _ready(c):
            pytest.skip("datastores not ready")
        email = f"u-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)

        bad = c.post("/api/auth/login", json={"email": email, "password": "wrong"})
        assert bad.status_code == 401
        assert c.get("/api/me").status_code == 401  # no session yet

        r = c.post("/api/auth/login", json={"email": email, "password": GOOD_PW})
        assert r.status_code == 200 and r.json()["user"]["email"] == email

        me = c.get("/api/me")
        assert me.status_code == 200
        assert me.json()["keys_set"] == {"soniox": False, "anthropic": False}

        rk = c.put("/api/me/keys", json={"soniox_key": "sk-secret-xyz"})
        assert rk.status_code == 200 and rk.json()["keys_set"]["soniox"] is True
        assert "sk-secret-xyz" not in c.get("/api/me").text  # never returns plaintext

        rs = c.put(
            "/api/me/settings",
            json={"default_translation_on": True, "default_output_language": "de"},
        )
        assert rs.json()["default_translation_on"] is True
        assert rs.json()["default_output_language"] == "de"

        # weak new password rejected by the BACKEND, good one accepted
        assert c.put(
            "/api/me/password", json={"current_password": GOOD_PW, "new_password": "weak"}
        ).status_code == 422
        assert c.put(
            "/api/me/password",
            json={"current_password": GOOD_PW, "new_password": "An0ther-Good-Pass!"},
        ).status_code == 200

        assert c.post("/api/auth/logout").status_code == 200
        assert c.get("/api/me").status_code == 401  # cookies cleared


def test_device_login_and_capture_token():
    with TestClient(app) as c:
        if not _ready(c):
            pytest.skip("datastores not ready")
        email = f"d-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)

        r = c.post(
            "/api/auth/login", json={"email": email, "password": GOOD_PW, "client": "device"}
        )
        assert r.status_code == 200
        device_token = r.json()["device_token"]

        rc = c.post(
            "/api/capture/token",
            headers={"Authorization": "Bearer " + device_token},
            json={"platform": "meet", "external_meeting_id": "acc-demo"},
        )
        assert rc.status_code == 200 and rc.json()["scope"] == "meet:acc-demo"

        # no device token -> 401
        assert c.post(
            "/api/capture/token", json={"platform": "meet", "external_meeting_id": "x"}
        ).status_code == 401


def test_password_change_invalidates_old_access_token():
    with TestClient(app) as c:
        if not _ready(c):
            pytest.skip("datastores not ready")
        email = f"pw-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        c.post("/api/auth/login", json={"email": email, "password": GOOD_PW})
        old_access = c.cookies.get("mn_session")
        changed = c.put(
            "/api/me/password",
            json={"current_password": GOOD_PW, "new_password": "An0ther-Good-Pass!"},
        )
        assert changed.status_code == 200
        assert c.get("/api/me").status_code == 200  # caller stays signed in (rotated)
        c.cookies.clear()
        # the OLD access JWT is rejected after the token_version bump
        assert c.get("/api/me", cookies={"mn_session": old_access}).status_code == 401


def test_refresh_reuse_detection_evicts_family():
    with TestClient(app) as c:
        if not _ready(c):
            pytest.skip("datastores not ready")
        email = f"rr-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        c.post("/api/auth/login", json={"email": email, "password": GOOD_PW})
        raw1 = c.cookies.get("mn_refresh")
        assert c.post("/api/auth/refresh").status_code == 200  # rotate -> raw2
        raw2 = c.cookies.get("mn_refresh")
        assert raw2 and raw2 != raw1
        c.cookies.clear()
        # replaying the OLD (revoked) refresh token is detected -> 401 + family eviction
        assert c.post("/api/auth/refresh", cookies={"mn_refresh": raw1}).status_code == 401
        c.cookies.clear()
        # the NEW token is now dead too (whole web-session family revoked)
        assert c.post("/api/auth/refresh", cookies={"mn_refresh": raw2}).status_code == 401
