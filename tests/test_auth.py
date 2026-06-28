"""Auth edge tests (spec v3 §15): token lifecycle, ingest WS gating, dev-token prod gate."""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth.dependencies import AUTH_SUBPROTOCOL
from app.auth.tokens import (
    AuthError,
    authorize_meeting,
    issue_capability_token,
    verify_capability_token,
)
from app.config import Settings, get_settings
from app.main import app, create_app

SECRET = "unit-test-secret-please-use-at-least-32-bytes"


def _tok(**kw) -> str:
    kw.setdefault("principal", "op")
    kw.setdefault("secret", SECRET)
    kw.setdefault("ttl_s", 60)
    return issue_capability_token(**kw)


def _raw(claims: dict) -> str:
    return jwt.encode(claims, SECRET, algorithm="HS256")


# --- token lifecycle ---


def test_roundtrip_and_scope():
    claims = verify_capability_token(_tok(meetings=["meet:m1"]), secret=SECRET)
    assert claims.principal == "op"
    assert authorize_meeting(claims, "meet", "m1")
    assert not authorize_meeting(claims, "meet", "m2")
    assert not authorize_meeting(claims, "teams", "m1")  # platform-scoped: no cross-platform IDOR


def test_wildcard_meetings():
    claims = verify_capability_token(_tok(), secret=SECRET)
    assert authorize_meeting(claims, "meet", "anything")
    assert authorize_meeting(claims, "teams", "anything")


def test_expired_rejected():
    with pytest.raises(AuthError):
        verify_capability_token(_tok(ttl_s=-10), secret=SECRET)


def test_tampered_rejected():
    with pytest.raises(AuthError):
        verify_capability_token(_tok() + "x", secret=SECRET)


def test_wrong_secret_rejected():
    with pytest.raises(AuthError):
        verify_capability_token(_tok(), secret="a-different-secret-of-at-least-32-bytes!")


def test_missing_token_rejected():
    with pytest.raises(AuthError):
        verify_capability_token("", secret=SECRET)


def test_no_exp_rejected():
    now = int(time.time())
    token = _raw({"sub": "op", "scope": "capture", "meetings": ["*"], "iat": now})
    with pytest.raises(AuthError):
        verify_capability_token(token, secret=SECRET)


def test_wrong_scope_rejected():
    now = int(time.time())
    token = _raw({"sub": "op", "scope": "nope", "meetings": ["*"], "iat": now, "exp": now + 60})
    with pytest.raises(AuthError):
        verify_capability_token(token, secret=SECRET)


def test_empty_subject_rejected():
    now = int(time.time())
    token = _raw({"sub": "", "scope": "capture", "meetings": ["*"], "iat": now, "exp": now + 60})
    with pytest.raises(AuthError):
        verify_capability_token(token, secret=SECRET)


@pytest.mark.parametrize("meetings", [None, "m1", 123, [1, 2], ["ok", 5]])
def test_malformed_meetings_rejected(meetings):
    now = int(time.time())
    payload = {"sub": "op", "scope": "capture", "iat": now, "exp": now + 60}
    if meetings is not None:
        payload["meetings"] = meetings
    with pytest.raises(AuthError):
        verify_capability_token(_raw(payload), secret=SECRET)


# --- dev-token endpoint + prod gate ---


def test_dev_token_mints_usable_token():
    with TestClient(app) as client:  # default app_env=local
        resp = client.post("/api/auth/dev-token", json={"principal": "op", "meetings": ["m1"]})
        assert resp.status_code == 200
        settings = get_settings()
        claims = verify_capability_token(
            resp.json()["token"], secret=settings.auth_secret, algorithm=settings.auth_algorithm
        )
        assert claims.principal == "op"
        assert claims.meetings == ["m1"]


def test_dev_token_absent_in_prod():
    prod = create_app(
        Settings(_env_file=None, app_env="prod", auth_secret="x" * 40, secret_key="y" * 40)
    )
    with TestClient(prod) as client:
        assert client.post("/api/auth/dev-token", json={}).status_code == 404


# --- ingest WS gating ---


def test_ingest_rejects_without_token():
    with TestClient(app) as client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ingest"):
            pass


def test_ingest_rejects_bad_token():
    with TestClient(app) as client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ingest?token=not-a-real-token"):
            pass


def _scoped_token(meetings: list[str]) -> str:
    settings = get_settings()
    return issue_capability_token(
        principal="op",
        secret=settings.auth_secret,
        algorithm=settings.auth_algorithm,
        ttl_s=60,
        meetings=meetings,
    )


def _hello(meeting: str, call_id: str) -> dict:
    return {
        "type": "hello",
        "platform": "meet",
        "external_meeting_id": meeting,
        "call_id": call_id,
    }


def test_ingest_forbidden_for_unauthorized_meeting():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        token = _scoped_token(["meet:allowed-mtg"])
        with client.websocket_connect(f"/ingest?token={token}") as ws:
            ws.send_json(_hello("a-different-mtg", f"fb-{uuid.uuid4().hex[:8]}"))
            msg = ws.receive_json()
            assert msg["type"] == "forbidden"
            assert msg["reason"] == "meeting_not_authorized"


def test_ingest_admits_authorized_meeting():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        token = _scoped_token(["meet:allowed-mtg"])
        call_id = f"ok-{uuid.uuid4().hex[:8]}"
        with client.websocket_connect(f"/ingest?token={token}") as ws:
            ws.send_json(_hello("allowed-mtg", call_id))
            msg = ws.receive_json()
            assert msg["type"] == "admitted"
            assert msg["call_id"] == call_id


def test_ingest_admits_via_subprotocol():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        token = _scoped_token(["meet:allowed-mtg"])
        with client.websocket_connect("/ingest", subprotocols=[AUTH_SUBPROTOCOL, token]) as ws:
            ws.send_json(_hello("allowed-mtg", f"sp-{uuid.uuid4().hex[:8]}"))
            assert ws.receive_json()["type"] == "admitted"


def test_ingest_protocol_error_on_non_hello():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        with client.websocket_connect(f"/ingest?token={_scoped_token(['*'])}") as ws:
            ws.send_json({"type": "not-hello"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["reason"] == "expected_hello"
