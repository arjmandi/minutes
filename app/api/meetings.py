"""Read API + live fan-out (spec v3 §12), owner-scoped to the signed-in user.

- GET /meetings — meetings the user owns (admin: all).
- GET /meetings/{id}/transcript?after=<seq> — durable final segments + translations, ordered.
- WS  /meetings/{id}/live — relays interim/final/translation events (session-cookie authed).
- POST /consent, DELETE /{id} — owner-or-admin.

Web auth is the session cookie (require_user / ws_user). The capture ingest WS keeps its capability
tokens — the extension mints one via /api/capture/token, which also claims the meeting's owner.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app import fanout
from app.auth.dependencies import require_user, ws_user
from app.db import repo
from app.db.models import ConsentStatus, Meeting, User
from app.logging import get_logger

router = APIRouter(prefix="/meetings", tags=["meetings"])
log = get_logger("meetings")


def _authorized(meeting: Meeting, user: User) -> bool:
    """Owner-scoped: the owner or an admin. Unowned meetings are admin-only."""
    return user.is_admin or (meeting.owner_id is not None and meeting.owner_id == user.id)


@router.get("")
async def list_meetings(request: Request, user: User = Depends(require_user)) -> list[dict]:
    async with request.app.state.session_factory() as db:
        meetings = await repo.list_meetings_for_user(db, user_id=user.id, is_admin=user.is_admin)
    return [
        {
            "id": str(m.id),
            "platform": m.platform.value,
            "external_meeting_id": m.external_meeting_id,
            "title": m.title,
            "created_at": m.created_at.isoformat(),
        }
        for m in meetings
    ]


@router.get("/{meeting_id}/transcript")
async def get_transcript(
    meeting_id: uuid.UUID,
    request: Request,
    after: int = 0,
    limit: int = 500,
    user: User = Depends(require_user),
) -> list[dict]:
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
        if meeting is None or not _authorized(meeting, user):
            raise HTTPException(status_code=404, detail="not found")  # 404 — don't leak existence
        segments = await repo.transcript_for_meeting(
            db, meeting_id, after_seq=after, limit=min(max(limit, 1), 1000)
        )
        return [
            {
                "id": str(s.id),
                "meeting_seq": s.meeting_seq,
                "speaker_id": s.speaker_id,
                "start_ts": s.start_ts,
                "end_ts": s.end_ts,
                "text": s.text,
                "source_language": s.source_language,
                "translations": [
                    {"target_language": t.target_language, "text": t.text} for t in s.translations
                ],
            }
            for s in segments
        ]


class ConsentBody(BaseModel):
    platform: str
    external_meeting_id: str
    status: str  # one of ConsentStatus: pending | granted | denied | withdrawn


@router.post("/consent")
async def set_consent(
    body: ConsentBody, request: Request, user: User = Depends(require_user)
) -> dict:
    """Record consent (first-class GDPR state); claims/owns the meeting for the user."""
    try:
        status = ConsentStatus(body.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid consent status") from exc
    async with request.app.state.session_factory() as db:
        existing = await repo.get_meeting_by_identity(
            db, platform=body.platform, external_meeting_id=body.external_meeting_id
        )
        if existing is not None and not _authorized(existing, user):
            raise HTTPException(status_code=403, detail="forbidden")
        meeting = await repo.set_consent(
            db,
            platform=body.platform,
            external_meeting_id=body.external_meeting_id,
            status=status,
            owner_id=user.id,
        )
        await db.commit()
        return {"id": str(meeting.id), "consent_status": meeting.consent_status.value}


@router.delete("/{meeting_id}")
async def erase_meeting(
    meeting_id: uuid.UUID, request: Request, user: User = Depends(require_user)
) -> dict:
    """GDPR erasure (owner-or-admin). Delete the meeting's audio objects, then its rows (cascade).
    Aborts BEFORE deleting rows if any object delete genuinely fails, so PII audio is never orphaned
    beyond the reach of both erasure and retention."""
    storage = request.app.state.storage
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
        if meeting is None or not _authorized(meeting, user):
            raise HTTPException(status_code=404, detail="not found")
        keys = await repo.chunk_keys_for_meeting(db, meeting_id)
    failed = 0
    for key in keys:
        try:
            await storage.delete(key)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("erase.object_delete_failed", key=key, error=repr(exc))
    if failed:
        # Keep the rows (the only record of the s3_keys) so erasure can be retried / reconciled.
        log.error("erase.partial", meeting_id=str(meeting_id), failed=failed, total=len(keys))
        raise HTTPException(status_code=502, detail="object deletion failed; erasure not completed")
    async with request.app.state.session_factory() as db:
        deleted = await repo.delete_meeting(db, meeting_id)
        await db.commit()
    log.info("erase.done", meeting_id=str(meeting_id), objects=len(keys), deleted=deleted)
    return {"deleted": deleted, "objects_deleted": len(keys), "objects_failed": failed}


@router.websocket("/{meeting_id}/live")
async def live(ws: WebSocket, meeting_id: uuid.UUID) -> None:
    user = await ws_user(ws)
    if user is None:
        await ws.close(code=1008, reason="unauthorized")
        return
    async with ws.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
    if meeting is None or not _authorized(meeting, user):
        await ws.close(code=1008, reason="forbidden")
        return

    await ws.accept()
    redis = ws.app.state.redis
    key = fanout.stream_key(meeting_id)
    last_id = "$"  # new events only; clients backfill durable history via the transcript endpoint
    disconnect = asyncio.create_task(_await_disconnect(ws))
    try:
        while not disconnect.done():
            try:
                resp = await redis.xread({key: last_id}, block=2000, count=100)
            except Exception as exc:  # noqa: BLE001 — Redis hiccup: degrade to no live updates
                log.warning("live.xread_failed", error=repr(exc))
                break
            for _stream, entries in resp or []:
                for entry_id, fields in entries:
                    last_id = entry_id
                    await ws.send_text(fields.get("data", "{}"))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        disconnect.cancel()
        await asyncio.gather(disconnect, return_exceptions=True)


async def _await_disconnect(ws: WebSocket) -> None:
    try:
        while True:
            if (await ws.receive())["type"] == "websocket.disconnect":
                return
    except (WebSocketDisconnect, RuntimeError):
        return
