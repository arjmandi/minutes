"""Control-plane tests (spec v3 §8): authenticated/authorized/allow-listed set_config,
cross-worker Redis routing, and LIVE application of translation targets through the manager.

Require Postgres + Redis; skip otherwise. Use the fake transcriber/translator/storage.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
import redis as redis_sync
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import select

from app import control
from app.audio.frames import encode_frame
from app.auth.tokens import issue_capability_token
from app.config import Settings, get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.db.models import Session, TranscriptSegment, Translation
from app.main import app
from app.session.events import AudioFrame, EndReason, SessionEnded, SessionStarted
from app.session.manager import SessionManager
from app.storage.factory import make_storage
from app.transcribe.factory import make_transcriber_factory
from app.translate.factory import make_translator_factory

_REDIS_URL = os.environ.get("MINUTES_REDIS_URL", "redis://localhost:6379/0")


def _token(meetings: list[str] | None = None) -> str:
    s = get_settings()
    return issue_capability_token(
        principal="op",
        secret=s.auth_secret,
        algorithm=s.auth_algorithm,
        ttl_s=60,
        meetings=meetings or ["*"],
    )


def _bearer(meetings: list[str] | None = None) -> dict:
    return {"Authorization": "Bearer " + _token(meetings)}


def _capture(client: TestClient, ext: str, call_id: str) -> None:
    """Run a full capture, leaving the session ENDED."""
    with client.websocket_connect(f"/ingest?token={_token()}") as ws:
        ws.send_json(
            {"type": "hello", "platform": "meet", "external_meeting_id": ext, "call_id": call_id}
        )
        assert ws.receive_json()["type"] == "admitted"
        ws.send_bytes(encode_frame(0, 0, b"\x00\x00" * 160))
        ws.send_json({"type": "end"})
        assert ws.receive_json()["type"] == "ended"


def _seed_active_session(ext: str, call_id: str) -> None:
    """Insert a meeting + an ACTIVE session row directly (control plane targets live sessions)."""

    async def _seed() -> None:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        async with factory() as db:
            meeting = await repo.upsert_meeting(db, platform="meet", external_meeting_id=ext)
            await repo.create_session(
                db, meeting_id=meeting.id, platform_call_id=call_id, run_id="w-test"
            )
            await db.commit()
        await engine.dispose()

    asyncio.run(_seed())


def test_set_config_accepts_audits_and_increments_generation():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        ext = f"cfg-{uuid.uuid4().hex[:8]}"
        call_id = f"cc-{uuid.uuid4().hex[:8]}"
        _seed_active_session(ext, call_id)

        r1 = client.post(
            f"/sessions/{call_id}/config",
            headers=_bearer(),
            json={"translation_targets": ["de", "es"]},
        )
        assert r1.status_code == 200
        assert r1.json() == {"config_generation": 1, "applied": True}

        r2 = client.post(
            f"/sessions/{call_id}/config",
            headers=_bearer(),
            json={"custom_vocabulary": ["Kubernetes", "Soniox"]},
        )
        assert r2.status_code == 200
        assert r2.json()["config_generation"] == 2  # monotonic per session


def test_set_config_requires_auth():
    with TestClient(app) as client:
        resp = client.post("/sessions/whatever/config", json={"translation_targets": ["de"]})
        assert resp.status_code == 401


def test_set_config_forbidden_for_other_meeting():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        ext = f"cfg-{uuid.uuid4().hex[:8]}"
        call_id = f"cc-{uuid.uuid4().hex[:8]}"
        _capture(client, ext, call_id)
        # Token scoped to a different meeting -> authz denies (cross-meeting IDOR guard).
        resp = client.post(
            f"/sessions/{call_id}/config",
            headers=_bearer(meetings=["meet:someone-else"]),
            json={"translation_targets": ["de"]},
        )
        assert resp.status_code == 403


def test_set_config_unknown_session_404():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        resp = client.post(
            f"/sessions/no-such-{uuid.uuid4().hex[:8]}/config",
            headers=_bearer(),
            json={"translation_targets": ["de"]},
        )
        assert resp.status_code == 404


def test_set_config_validation_rejects_out_of_bounds():
    with TestClient(app) as client:
        # Validation runs before any lookup, so no live session is required.
        too_many = client.post(
            "/sessions/x/config",
            headers=_bearer(),
            json={"translation_targets": [f"l{i}" for i in range(11)]},
        )
        assert too_many.status_code == 422
        huge_vocab = client.post(
            "/sessions/x/config",
            headers=_bearer(),
            json={"custom_vocabulary": ["a" * 11000]},
        )
        assert huge_vocab.status_code == 422


def test_set_config_publishes_to_owning_worker_channel():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        ext = f"cfg-{uuid.uuid4().hex[:8]}"
        call_id = f"cc-{uuid.uuid4().hex[:8]}"
        _seed_active_session(ext, call_id)

        sub = redis_sync.from_url(_REDIS_URL)
        ps = sub.pubsub(ignore_subscribe_messages=True)
        ps.subscribe(control.channel_key(call_id))  # subscribe BEFORE the publish (no backlog)
        try:
            resp = client.post(
                f"/sessions/{call_id}/config",
                headers=_bearer(),
                json={"translation_targets": ["de"]},
            )
            assert resp.status_code == 200
            msg = None
            for _ in range(50):
                m = ps.get_message(timeout=0.1)
                if m and m.get("type") == "message":
                    msg = m
                    break
        finally:
            ps.close()
            sub.close()
        assert msg is not None
        payload = json.loads(msg["data"])
        assert payload["translation_targets"] == ["de"]
        assert payload["config_generation"] >= 1


def test_set_config_rejects_ended_session():
    with TestClient(app) as client:
        if client.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        ext = f"cfg-{uuid.uuid4().hex[:8]}"
        call_id = f"cc-{uuid.uuid4().hex[:8]}"
        _capture(client, ext, call_id)  # session is now ENDED
        resp = client.post(
            f"/sessions/{call_id}/config",
            headers=_bearer(),
            json={"translation_targets": ["de"]},
        )
        assert resp.status_code == 409  # a dead session can't be reconfigured live


class _ScriptedAdapter:
    """Emits SessionStarted, then audio frames until told to stop, then SessionEnded."""

    def __init__(self, platform: str, ext: str, call_id: str, stop: asyncio.Event) -> None:
        self._platform = platform
        self._ext = ext
        self._call_id = call_id
        self._stop = stop

    @property
    def call_id(self) -> str:
        return self._call_id

    async def events(self):
        yield SessionStarted(self._platform, self._ext, self._call_id)
        i = 0
        while not self._stop.is_set():
            yield AudioFrame(pcm=b"\x01\x02" * 160, timestamp=i * 0.02)
            await asyncio.sleep(0.02)
            i += 1
        yield SessionEnded(EndReason.normal)


async def _run_live() -> object:
    settings = Settings(
        _env_file=None, app_env="test", soniox_api_key="", anthropic_api_key=""
    )
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis.ping()
        async with factory() as db:
            await repo.list_recent_meetings(db, limit=1)
    except Exception:  # noqa: BLE001 — datastores not available
        await redis.aclose()
        await engine.dispose()
        return "skip"

    manager = SessionManager(
        session_factory=factory,
        redis=redis,
        transcriber_factory=make_transcriber_factory(settings),
        translator_factory=make_translator_factory(settings),
        translation_targets=["de"],  # initial target; control will switch it live
        storage=make_storage(settings),
        chunk_interval_s=300,
        worker_id="w-test",
    )
    call_id = f"live-{uuid.uuid4().hex[:8]}"
    ext = f"live-{uuid.uuid4().hex[:8]}"
    stop = asyncio.Event()
    run_task = asyncio.create_task(manager.run(_ScriptedAdapter("meet", ext, call_id, stop)))

    new_target = "es"
    found = False
    try:
        for _ in range(250):  # ~5s budget for local PG+Redis
            async with factory() as db:
                session = await repo.get_session_by_call_id(db, call_id)
            if session is not None:
                # Republish to beat the subscribe race (idempotent — same target).
                await control.publish(
                    redis, call_id, {"translation_targets": [new_target], "config_generation": 1}
                )
                async with factory() as db:
                    hit = await db.execute(
                        select(Translation.id)
                        .join(TranscriptSegment, Translation.segment_id == TranscriptSegment.id)
                        .join(Session, TranscriptSegment.session_id == Session.id)
                        .where(
                            Session.platform_call_id == call_id,
                            Translation.target_language == new_target,
                        )
                        .limit(1)
                    )
                    if hit.first() is not None:
                        found = True
                        break
            await asyncio.sleep(0.02)
    finally:
        stop.set()
        await asyncio.wait_for(run_task, timeout=10)
        await redis.aclose()
        await engine.dispose()
    return found


def test_set_config_applies_translation_targets_live():
    result = asyncio.run(_run_live())
    if result == "skip":
        pytest.skip("datastores not ready")
    assert result is True  # a post-config segment was translated to the new target
