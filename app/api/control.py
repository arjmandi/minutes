"""Real-time control plane (spec v3 §8): authenticated, authorized, allow-listed set_config.

Three ordered checks: authn (require_claims) -> authz (principal owns the session's meeting) ->
schema + value allow-list/bounds (deterministic; no model interprets intent). Accepted changes are
audited (config_changes) and routed to the owning worker via Redis (app.control).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import control
from app.auth.dependencies import require_claims
from app.auth.tokens import Claims, authorize_meeting
from app.db import repo
from app.db.models import SessionStatus

router = APIRouter(prefix="/sessions", tags=["control"])

_MAX_LANGS = 10
_MAX_VOCAB_TERMS = 500
_MAX_VOCAB_CHARS = 10000  # Soniox context cap


class ConfigBody(BaseModel):
    translation_targets: list[str] | None = None
    custom_vocabulary: list[str] | None = None
    source_language_hints: list[str] | None = None


def _valid_langs(items: list[str]) -> bool:
    return len(items) <= _MAX_LANGS and all(
        isinstance(x, str) and 2 <= len(x) <= 16 for x in items
    )


@router.post("/{call_id}/config")
async def set_config(
    call_id: str, body: ConfigBody, request: Request, claims: Claims = Depends(require_claims)
) -> dict:
    if body.translation_targets is not None and not _valid_langs(body.translation_targets):
        raise HTTPException(status_code=422, detail="invalid translation_targets")
    if body.source_language_hints is not None and not _valid_langs(body.source_language_hints):
        raise HTTPException(status_code=422, detail="invalid source_language_hints")
    if body.custom_vocabulary is not None:
        vocab = body.custom_vocabulary
        if (
            not all(isinstance(t, str) for t in vocab)
            or len(vocab) > _MAX_VOCAB_TERMS
            or sum(len(t) for t in vocab) > _MAX_VOCAB_CHARS
        ):
            raise HTTPException(status_code=422, detail="invalid custom_vocabulary")

    async with request.app.state.session_factory() as db:
        session = await repo.get_session_by_call_id(db, call_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        meeting = await repo.get_meeting(db, session.meeting_id)
        if meeting is None or not authorize_meeting(
            claims, meeting.platform.value, meeting.external_meeting_id
        ):
            raise HTTPException(status_code=403, detail="forbidden")
        # Liveness gate AFTER authz (don't leak liveness to an unauthorized caller). A config change
        # against a dead session can never be applied live, so reject rather than report applied.
        if session.status in (SessionStatus.ended, SessionStatus.failed):
            raise HTTPException(status_code=409, detail="session not live")
        generation = await repo.insert_config_change(
            db,
            session_id=session.id,
            scope="session",
            source_language_hints=body.source_language_hints,
            custom_vocabulary=body.custom_vocabulary,
            translation_targets=body.translation_targets,
            actor=claims.principal,
        )
        await db.commit()

    await control.publish(
        request.app.state.redis,
        call_id,
        {
            "config_generation": generation,
            "translation_targets": body.translation_targets,
            "custom_vocabulary": body.custom_vocabulary,
            "source_language_hints": body.source_language_hints,
        },
    )
    return {"config_generation": generation, "applied": True}
