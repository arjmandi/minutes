"""Anonymous public share-link read access (spec v3 §18).

A meeting carrying a ``share_token`` is readable, read-only, by anyone holding that opaque token —
no auth, no owner/identity leakage. Mirrors the owner transcript/export shape but exposes only safe
fields (title, platform, timing, transcript, translations) — never owner, external id, or consent.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.meetings import (
    _all_segments,
    _export_payload,
    _export_response,
    _parse_source,
    _segment_dict,
    _validate_export_params,
    public_meeting_dict,
)
from app.db import repo

router = APIRouter(prefix="/shared", tags=["shared"])


@router.get("/{token}")
async def shared_meeting(token: str, request: Request) -> dict:
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting_by_share_token(db, token)
        if meeting is None:
            raise HTTPException(status_code=404, detail="not found")
        return public_meeting_dict(meeting)


@router.get("/{token}/transcript")
async def shared_transcript(
    token: str, request: Request, after: int = 0, limit: int = 500
) -> list[dict]:
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting_by_share_token(db, token)
        if meeting is None:
            raise HTTPException(status_code=404, detail="not found")
        segments = await repo.transcript_for_meeting(
            db, meeting.id, after_seq=after, limit=min(max(limit, 1), 1000)
        )
        return [_segment_dict(seg, joined_at, s) for seg, joined_at, s in segments]


@router.get("/{token}/export")
async def shared_export(
    token: str,
    request: Request,
    format: str = "txt",
    include: str = "both",
    timestamps: bool = True,
    lang: str | None = None,
    source: str | None = None,  # tab | mic | both | absent(=all -> labeled sections)
):
    """Public export of a shared meeting: query params set format, content, and source."""
    _validate_export_params(format, include, source)
    src_filter = None if source in (None, "both") else _parse_source(source)
    async with request.app.state.session_factory() as db:
        meeting = await repo.get_meeting_by_share_token(db, token)
        if meeting is None:
            raise HTTPException(status_code=404, detail="not found")
        rows = await _all_segments(db, meeting.id, source=src_filter)
        payload = _export_payload(
            meeting, rows, fmt=format, include=include, timestamps=timestamps, lang=lang,
            public=True,
        )
    return _export_response(meeting, payload, format, public=True)
