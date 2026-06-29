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
import re
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app import fanout
from app.auth.dependencies import require_user, ws_user
from app.auth.sessions import new_opaque_token
from app.db import repo
from app.db.models import ConsentStatus, Meeting, TranslationSource, TranslationStatus, User
from app.logging import get_logger
from app.translate.resolve import build_user_translator

router = APIRouter(prefix="/meetings", tags=["meetings"])
log = get_logger("meetings")


def _authorized(meeting: Meeting, user: User) -> bool:
    """Owner-scoped: the owner or an admin. Unowned meetings are admin-only."""
    return user.is_admin or (meeting.owner_id is not None and meeting.owner_id == user.id)


def _meeting_dict(m: Meeting) -> dict:
    return {
        "id": str(m.id),
        "platform": m.platform.value,
        "external_meeting_id": m.external_meeting_id,
        "title": m.title,
        "created_at": m.created_at.isoformat(),
        "translation": {
            "enabled": m.translation_enabled,
            "output_language": m.translation_output_language,
            "input_language": m.translation_input_language,
            "prompt": m.translation_prompt,
            "model": m.translation_model,
        },
        "share": {
            "enabled": m.share_token is not None,
            "token": m.share_token,
            "url": f"/shared/{m.share_token}" if m.share_token else None,
        },
    }


def public_meeting_dict(m: Meeting) -> dict:
    """Curated, anonymous-safe meeting view for public share links — NEVER owner id, external
    meeting id, consent, or the share token. Title/platform/timing + the translation language only.
    """
    return {
        "title": m.title,
        "platform": m.platform.value,
        "created_at": m.created_at.isoformat(),
        "translation": {
            "enabled": m.translation_enabled,
            "output_language": m.translation_output_language,
        },
    }


def _abs_ts(joined_at: datetime, offset: float | None) -> str | None:
    """Absolute wall-clock time of a segment offset within its session (media epoch = joined_at)."""
    return (joined_at + timedelta(seconds=offset)).isoformat() if offset is not None else None


def _segment_dict(seg, joined_at: datetime) -> dict:
    return {
        "id": str(seg.id),
        "meeting_seq": seg.meeting_seq,
        "speaker_id": seg.speaker_id,
        "start_ts": seg.start_ts,
        "end_ts": seg.end_ts,
        "started_at": _abs_ts(joined_at, seg.start_ts),
        "ended_at": _abs_ts(joined_at, seg.end_ts),
        "text": seg.text,
        "source_language": seg.source_language,
        "translations": [
            {
                "target_language": t.target_language,
                "text": t.text,
                "status": t.status.value,
                "source": t.source.value,
            }
            for t in seg.translations
        ],
    }


@router.get("")
async def list_meetings(request: Request, user: User = Depends(require_user)) -> list[dict]:
    async with request.app.state.session_factory() as db:
        meetings = await repo.list_meetings_for_user(db, user_id=user.id, is_admin=user.is_admin)
    return [_meeting_dict(m) for m in meetings]


@router.get("/{meeting_id}")
async def get_meeting_detail(
    meeting_id: uuid.UUID, request: Request, user: User = Depends(require_user)
) -> dict:
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
        if meeting is None or not _authorized(meeting, user):
            raise HTTPException(status_code=404, detail="not found")
        return _meeting_dict(meeting)


class RenameBody(BaseModel):
    title: str | None = None


@router.put("/{meeting_id}")
async def rename_meeting(
    meeting_id: uuid.UUID,
    body: RenameBody,
    request: Request,
    user: User = Depends(require_user),
) -> dict:
    """Rename a meeting (owner-or-admin)."""
    title = body.title.strip() if body.title and body.title.strip() else None
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
        if meeting is None or not _authorized(meeting, user):
            raise HTTPException(status_code=404, detail="not found")
        await repo.set_meeting_title(db, meeting_id=meeting_id, title=title)
        await db.commit()
        return _meeting_dict(await repo.get_meeting(db, meeting_id))


class ShareBody(BaseModel):
    rotate: bool = False  # mint a fresh token even if one exists (revoke prior links)


@router.post("/{meeting_id}/share")
async def enable_share(
    meeting_id: uuid.UUID,
    request: Request,
    body: ShareBody | None = None,
    user: User = Depends(require_user),
) -> dict:
    """Enable (or rotate) a meeting's public share link (owner-or-admin). Rotating mints a fresh
    token so previously-shared URLs stop resolving — this is how a link is revoked."""
    rotate = bool(body and body.rotate)
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
        if meeting is None or not _authorized(meeting, user):
            raise HTTPException(status_code=404, detail="not found")
        if meeting.share_token is None or rotate:
            await repo.set_meeting_share_token(
                db, meeting_id=meeting_id, share_token=new_opaque_token()
            )
            await db.commit()
        return _meeting_dict(await repo.get_meeting(db, meeting_id))


@router.delete("/{meeting_id}/share")
async def disable_share(
    meeting_id: uuid.UUID, request: Request, user: User = Depends(require_user)
) -> dict:
    """Disable a meeting's public share link (owner-or-admin); existing URLs stop resolving."""
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
        if meeting is None or not _authorized(meeting, user):
            raise HTTPException(status_code=404, detail="not found")
        await repo.set_meeting_share_token(db, meeting_id=meeting_id, share_token=None)
        await db.commit()
        return _meeting_dict(await repo.get_meeting(db, meeting_id))


def _validate_export_params(fmt: str, include: str) -> None:
    if fmt not in ("txt", "md", "json"):
        raise HTTPException(status_code=422, detail="format must be txt, md or json")
    if include not in ("transcript", "translation", "both"):
        raise HTTPException(
            status_code=422, detail="include must be transcript, translation or both"
        )


def _export_response(meeting: Meeting, payload, fmt: str, *, public: bool = False):
    """Build the download response (sanitized filename — title is user-controlled).

    public=True is the anonymous share path: the filename must never fall back to the external
    meeting id (it correlates to the real Teams/Meet call) — only the title, else a neutral name.
    """
    raw_stem = meeting.title or ("meeting" if public else meeting.external_meeting_id)
    stem = re.sub(r"[^A-Za-z0-9._ -]", "-", raw_stem or "").strip(" .-")[:80] or "meeting"
    disp = {"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'}
    if fmt == "json":
        return JSONResponse(payload, headers=disp)
    media = "text/markdown" if fmt == "md" else "text/plain"
    return PlainTextResponse(payload, headers=disp, media_type=media)


async def _all_segments(db, meeting_id: uuid.UUID) -> list[tuple]:
    """Page the full transcript (export needs every segment, not just the first page)."""
    out: list[tuple] = []
    after = 0
    while True:
        batch = await repo.transcript_for_meeting(db, meeting_id, after_seq=after, limit=1000)
        if not batch:
            break
        out.extend(batch)
        after = batch[-1][0].meeting_seq
        if len(batch) < 1000:
            break
    return out


def _export_payload(
    meeting: Meeting, rows: list[tuple], *, fmt, include, timestamps, lang, public: bool = False
):
    """Render the meeting transcript/translation as txt / markdown / json (spec v3 §16).

    public=True is the anonymous share path: the JSON meeting block is the curated public view
    (no owner id / external id / share token / private translation config) and the markdown header
    omits the external meeting id — matching the share-link contract.
    """
    lang = lang or meeting.translation_output_language
    epoch = min((j for _, j in rows), default=None)

    def stamp(seg, joined_at: datetime) -> str:
        if not timestamps or epoch is None:
            return ""
        total = int((joined_at + timedelta(seconds=seg.start_ts or 0) - epoch).total_seconds())
        total = max(total, 0)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"[{h:02d}:{m:02d}:{s:02d}] "

    def translation_of(seg) -> str:
        if not lang:
            return ""
        ok = [t.text for t in seg.translations if t.target_language == lang and t.text]
        return ok[0] if ok else ""

    title = meeting.title or ("Meeting" if public else meeting.external_meeting_id)

    if fmt == "json":
        return {
            "meeting": public_meeting_dict(meeting) if public else _meeting_dict(meeting),
            "segments": [_segment_dict(seg, j) for seg, j in rows],
        }

    lines: list[str] = []
    if fmt == "md":
        lines.append(f"# {title}")
        if not public:  # the external meeting id correlates to the real call — owner export only
            lines.append(f"_{meeting.platform.value} · {meeting.external_meeting_id}_\n")
    for seg, joined_at in rows:
        pre = stamp(seg, joined_at)
        spk = seg.speaker_id if seg.speaker_id and seg.speaker_id != "mixed" else ""
        head = f"{pre}{spk + ': ' if spk else ''}"
        if include == "transcript":
            lines.append(f"{head}{seg.text}")
        elif include == "translation":
            lines.append(f"{head}{translation_of(seg)}")
        else:  # both: source line, translation indented beneath it
            lines.append(f"{head}{seg.text}")
            tr = translation_of(seg)
            if tr:
                lines.append(f"{' ' * len(head)}{tr}")
    return "\n".join(lines)


@router.get("/{meeting_id}/export")
async def export_meeting(
    meeting_id: uuid.UUID,
    request: Request,
    format: str = "txt",
    include: str = "both",
    timestamps: bool = True,
    lang: str | None = None,
    user: User = Depends(require_user),
):
    """Export a meeting (owner-or-admin); see query params for format + included content."""
    _validate_export_params(format, include)
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
        if meeting is None or not _authorized(meeting, user):
            raise HTTPException(status_code=404, detail="not found")
        rows = await _all_segments(db, meeting_id)
        payload = _export_payload(
            meeting, rows, fmt=format, include=include, timestamps=timestamps, lang=lang
        )
    return _export_response(meeting, payload, format)


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
        return [_segment_dict(seg, joined_at) for seg, joined_at in segments]


class TranslationConfigBody(BaseModel):
    enabled: bool | None = None
    output_language: str | None = None
    input_language: str | None = None
    prompt: str | None = None
    model: str | None = None


@router.put("/{meeting_id}/translation")
async def set_translation_config(
    meeting_id: uuid.UUID,
    body: TranslationConfigBody,
    request: Request,
    user: User = Depends(require_user),
) -> dict:
    """Configure a meeting's translation (owner-or-admin). Applies to the next capture session +
    on-demand translation; a mid-meeting live toggle is a follow-up via the control plane.
    """
    fields = body.model_dump(exclude_unset=True)
    if "enabled" in fields:
        fields["translation_enabled"] = bool(fields.pop("enabled"))
    for src in ("output_language", "input_language", "prompt", "model"):
        if src in fields:
            fields[f"translation_{src}"] = fields.pop(src)
    # input_language is NOT NULL (defaults to "detect"); a null clear is a no-op, not an error.
    if fields.get("translation_input_language") is None:
        fields.pop("translation_input_language", None)
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting(db, meeting_id)
        if meeting is None or not _authorized(meeting, user):
            raise HTTPException(status_code=404, detail="not found")
        # Enabling requires a target language (here or already on the meeting).
        target = fields.get("translation_output_language", meeting.translation_output_language)
        if fields.get("translation_enabled") and not target:
            raise HTTPException(status_code=422, detail="output_language required to enable")
        await repo.set_meeting_translation_config(db, meeting_id=meeting_id, **fields)
        await db.commit()
        refreshed = await repo.get_meeting(db, meeting_id)
        return _meeting_dict(refreshed)


@router.post("/{meeting_id}/segments/{segment_id}/translate")
async def translate_segment(
    meeting_id: uuid.UUID,
    segment_id: uuid.UUID,
    request: Request,
    target_language: str | None = None,
    user: User = Depends(require_user),
) -> dict:
    """On-demand "translate this line" (owner-or-admin). Uses the meeting's owner's Anthropic key;
    422 if no key is configured. Persists as a manual translation and fans it out live."""
    settings = request.app.state.settings
    async with request.app.state.session_factory() as db:
        found = await repo.get_segment_for_translation(db, segment_id)
        if found is None or found[1].id != meeting_id or not _authorized(found[1], user):
            raise HTTPException(status_code=404, detail="not found")
        segment, meeting = found
        target = target_language or meeting.translation_output_language
        if not target:
            raise HTTPException(status_code=422, detail="no target language configured")
        # Translating to the source language is a no-op, not a failure — say so clearly.
        if segment.source_language and target == segment.source_language:
            raise HTTPException(
                status_code=422,
                detail=f"already in {target} — choose a different output language",
            )
        owner = (
            await repo.get_user_by_id(db, meeting.owner_id) if meeting.owner_id else None
        )
        translator = build_user_translator(
            owner, settings=settings, model=meeting.translation_model
        )
        if translator is None:
            raise HTTPException(status_code=422, detail="no Anthropic API key for this meeting")
        out = await translator.translate(
            segment.text, source_language=segment.source_language, target_languages=[target]
        )
        text = out.get(target, "")
        status = TranslationStatus.ok if target in out else TranslationStatus.failed
        await repo.upsert_translation(
            db,
            segment_id=segment_id,
            target_language=target,
            text=text,
            status=status,
            source=TranslationSource.manual,
        )
        await db.commit()
    await fanout.publish(
        request.app.state.redis,
        meeting_id,
        {
            "kind": "translation",
            "segment_id": str(segment_id),
            "target": target,
            "text": text,
            "status": status.value,
        },
    )
    return {"target_language": target, "text": text, "status": status.value}


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
        keys = await repo.object_keys_for_meeting(db, meeting_id)
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
