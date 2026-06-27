"""Read API + live fan-out (spec v3 §12).

- GET /meetings — recent meetings the principal is authorized for.
- GET /meetings/{id}/transcript?after=<seq> — durable final segments + translations, ordered.
- WS  /meetings/{id}/live — relays interim/final/translation events from the meeting's Redis
  stream. Clients backfill durable history via the transcript endpoint, then go live here.

All behind the auth edge with per-meeting authorization.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app import fanout
from app.auth.dependencies import require_claims, ws_token
from app.auth.tokens import AuthError, Claims, authorize_meeting, verify_capability_token
from app.db import repo
from app.db.models import ConsentStatus
from app.logging import get_logger

router = APIRouter(prefix="/meetings", tags=["meetings"])
log = get_logger("meetings")


@router.get("")
async def list_meetings(request: Request, claims: Claims = Depends(require_claims)) -> list[dict]:
    async with request.app.state.session_factory() as db:
        meetings = await repo.list_recent_meetings(db)
        return [
            {
                "id": str(m.id),
                "platform": m.platform.value,
                "external_meeting_id": m.external_meeting_id,
                "title": m.title,
                "created_at": m.created_at.isoformat(),
            }
            for m in meetings
            if authorize_meeting(claims, m.platform.value, m.external_meeting_id)
        ]


@router.get("/{meeting_id}/transcript")
async def get_transcript(
    meeting_id: uuid.UUID,
    request: Request,
    after: int = 0,
    limit: int = 500,
    claims: Claims = Depends(require_claims),
) -> list[dict]:
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="not found")
        if not authorize_meeting(claims, meeting.platform.value, meeting.external_meeting_id):
            raise HTTPException(status_code=403, detail="forbidden")
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
    body: ConsentBody, request: Request, claims: Claims = Depends(require_claims)
) -> dict:
    """Record consent for a meeting (first-class GDPR state). Upserts the meeting if new."""
    if not authorize_meeting(claims, body.platform, body.external_meeting_id):
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        status = ConsentStatus(body.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid consent status") from exc
    async with request.app.state.session_factory() as db:
        meeting = await repo.set_consent(
            db, platform=body.platform, external_meeting_id=body.external_meeting_id, status=status
        )
        await db.commit()
        return {"id": str(meeting.id), "consent_status": meeting.consent_status.value}


@router.delete("/{meeting_id}")
async def erase_meeting(
    meeting_id: uuid.UUID, request: Request, claims: Claims = Depends(require_claims)
) -> dict:
    """GDPR erasure: delete the meeting's audio objects, then its rows (DB cascade)."""
    storage = request.app.state.storage
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="not found")
        if not authorize_meeting(claims, meeting.platform.value, meeting.external_meeting_id):
            raise HTTPException(status_code=403, detail="forbidden")
        keys = await repo.chunk_keys_for_meeting(db, meeting_id)
    for key in keys:  # delete objects first; the row deletion below is the durable record of intent
        try:
            await storage.delete(key)
        except Exception as exc:  # noqa: BLE001
            log.warning("erase.object_delete_failed", key=key, error=repr(exc))
    async with request.app.state.session_factory() as db:
        deleted = await repo.delete_meeting(db, meeting_id)
        await db.commit()
    log.info("erase.done", meeting_id=str(meeting_id), objects=len(keys), deleted=deleted)
    return {"deleted": deleted, "objects_deleted": len(keys)}


@router.websocket("/{meeting_id}/live")
async def live(ws: WebSocket, meeting_id: uuid.UUID) -> None:
    settings = ws.app.state.settings
    token, subprotocol = ws_token(ws)
    try:
        claims = verify_capability_token(
            token or "", secret=settings.auth_secret, algorithm=settings.auth_algorithm
        )
    except AuthError:
        await ws.close(code=1008, reason="unauthorized")
        return

    async with ws.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
    if meeting is None or not authorize_meeting(
        claims, meeting.platform.value, meeting.external_meeting_id
    ):
        await ws.close(code=1008, reason="forbidden")
        return

    await ws.accept(subprotocol=subprotocol)
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
