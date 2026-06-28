"""Read API tests: transcript retrieval, per-meeting authorization, translations.

Drives the ingest pipeline (FakeTranscriber/FakeTranslator) then reads it back. Requires
Postgres + Redis; skips otherwise.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.audio.frames import encode_frame
from app.auth.tokens import issue_capability_token
from app.config import get_settings
from app.main import app


def _token(meetings: list[str]) -> str:
    settings = get_settings()
    return issue_capability_token(
        principal="op",
        secret=settings.auth_secret,
        algorithm=settings.auth_algorithm,
        ttl_s=60,
        meetings=meetings,
    )


def _bearer(token: str) -> dict:
    return {"Authorization": "Bearer " + token}


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
        wildcard = _token(["*"])
        _capture(client, wildcard, ext, frames=2)

        meetings = client.get("/api/meetings", headers=_bearer(wildcard)).json()
        meeting = next(m for m in meetings if m["external_meeting_id"] == ext)

        resp = client.get(f"/api/meetings/{meeting['id']}/transcript", headers=_bearer(wildcard))
        assert resp.status_code == 200
        segs = resp.json()
        assert len(segs) == 2
        assert [s["meeting_seq"] for s in segs] == sorted(s["meeting_seq"] for s in segs)

        non_en = [t for t in get_settings().translation_targets if t != "en"]
        if non_en:
            assert all(len(s["translations"]) == len(non_en) for s in segs)

        # Authorization: a token scoped to a different meeting can't read this one, and doesn't
        # see it listed.
        other = _token(["a-different-meeting"])
        forbidden = client.get(f"/api/meetings/{meeting['id']}/transcript", headers=_bearer(other))
        assert forbidden.status_code == 403
        listed = client.get("/api/meetings", headers=_bearer(other)).json()
        assert all(m["external_meeting_id"] != ext for m in listed)
