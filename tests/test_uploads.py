"""Audio upload -> async file transcription (spec v3 §17).

Exercises the full owner-scoped flow: upload an audio file, the out-of-band worker (run directly)
transcribes it (key-less dev fake) into a single-session upload meeting, plus cancel + validation +
authorization. DB + Redis required; skips otherwise.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.db.models import JobStatus
from app.jobs import transcription
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


def _audio(n: int) -> dict:
    return {"file": ("meeting.wav", b"\x00" * n, "audio/wav")}


def test_upload_transcribe_flow():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"up-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        _login(c, email)

        r = c.post("/api/uploads", files=_audio(64))
        assert r.status_code == 200
        job = r.json()
        assert job["status"] == "queued"
        jid, mid = job["id"], job["meeting_id"]

        meeting = c.get(f"/api/meetings/{mid}").json()
        assert meeting["platform"] == "upload" and meeting["title"] == "meeting.wav"

        # Process the queue out-of-band (the worker builds its own engine on this loop).
        assert asyncio.run(transcription.run()) >= 1

        assert c.get(f"/api/uploads/{jid}").json()["status"] == "done"
        segs = c.get(f"/api/meetings/{mid}/transcript").json()
        assert len(segs) == 4  # 64 bytes // 16 -> 4 fake segments
        assert segs[0]["text"].startswith("uploaded utterance")
        assert any(j["id"] == jid for j in c.get("/api/uploads").json())


def test_upload_cancel():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"uc-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        _login(c, email)

        jid = c.post("/api/uploads", files=_audio(32)).json()["id"]
        assert c.delete(f"/api/uploads/{jid}").json()["status"] == "canceled"
        asyncio.run(transcription.run())  # claim skips canceled jobs
        assert c.get(f"/api/uploads/{jid}").json()["status"] == "canceled"
        assert c.delete(f"/api/uploads/{jid}").status_code == 409  # already finished


def test_upload_validation_and_authz():
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")
        email = f"uv-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(email)
        _login(c, email)

        assert c.post(
            "/api/uploads", files={"file": ("e.wav", b"", "audio/wav")}
        ).status_code == 422
        assert c.post(
            "/api/uploads", files={"file": ("n.txt", b"hello", "text/plain")}
        ).status_code == 415

        jid = c.post("/api/uploads", files=_audio(16)).json()["id"]
        other = f"oo-{uuid.uuid4().hex[:8]}@test.io"
        _make_user(other)
        c.post("/api/auth/logout")
        _login(c, other)
        assert c.get(f"/api/uploads/{jid}").status_code == 404
        assert c.delete(f"/api/uploads/{jid}").status_code == 404


def test_cancel_during_processing_beats_done():
    """A cancel that lands while a job is processing must win: the guarded done-transition
    (repo.finish_job, WHERE status='processing') cannot resurrect a canceled job."""
    with TestClient(app) as c:
        if c.get("/readyz").status_code != 200:
            pytest.skip("datastores not ready")

    async def _run() -> None:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as db:
                user = await repo.create_user(
                    db, email=f"cp-{uuid.uuid4().hex[:8]}@test.io", password_hash=hash_password(PW)
                )
                meeting = await repo.upsert_meeting(
                    db,
                    platform="upload",
                    external_meeting_id=f"upload:{uuid.uuid4().hex}",
                    owner_id=user.id,
                )
                job = await repo.create_transcription_job(
                    db,
                    owner_id=user.id,
                    meeting_id=meeting.id,
                    s3_key="uploads/x/source",
                    original_filename="x.wav",
                    content_type="audio/wav",
                    size_bytes=10,
                )
                await db.commit()
                jid = job.id

            async with factory() as db:  # queued -> processing (claim broadly to avoid ordering)
                claimed = await repo.claim_queued_jobs(db, run_id="r-test", limit=100)
                await db.commit()
            assert any(j.id == jid for j in claimed)

            async with factory() as db:  # cancel mid-processing
                assert await repo.cancel_job(db, job_id=jid) is True
                await db.commit()

            async with factory() as db:  # guarded done-transition must be a no-op now
                assert await repo.finish_job(db, job_id=jid, status=JobStatus.done) is False
                await db.commit()

            async with factory() as db:
                job = await repo.get_transcription_job(db, jid)
                assert job is not None and job.status == JobStatus.canceled
        finally:
            await engine.dispose()

    asyncio.run(_run())
