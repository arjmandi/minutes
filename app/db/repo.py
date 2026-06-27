"""Async persistence helpers (spec v3 §10-11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Meeting,
    Platform,
    Session,
    SessionStatus,
    TranscriptSegment,
    Translation,
)


async def upsert_meeting(db: AsyncSession, *, platform: str, external_meeting_id: str) -> Meeting:
    """DB-arbitrated identity. A no-op DO UPDATE guarantees a row is RETURNING'd in one
    statement, avoiding the lost-update / NoResultFound race of insert-then-select."""
    stmt = (
        pg_insert(Meeting)
        .values(platform=Platform(platform), external_meeting_id=external_meeting_id)
        .on_conflict_do_update(
            index_elements=["platform", "external_meeting_id"],
            set_={"external_meeting_id": external_meeting_id},
        )
        .returning(Meeting.id)
    )
    meeting_id = (await db.execute(stmt)).scalar_one()
    meeting = await db.get(Meeting, meeting_id)
    assert meeting is not None
    return meeting


async def create_session(
    db: AsyncSession,
    *,
    meeting_id: uuid.UUID,
    platform_call_id: str,
    run_id: str,
    config_snapshot: dict | None = None,
) -> Session:
    """Create the session row, or take over a prior session with the same platform_call_id
    (sequential reconnect): reclaim it under our run_id and re-activate."""
    session = Session(
        meeting_id=meeting_id,
        platform_call_id=platform_call_id,
        status=SessionStatus.active,
        run_id=run_id,
        config_snapshot=config_snapshot,
    )
    try:
        async with db.begin_nested():  # savepoint: an IntegrityError won't poison the outer txn
            db.add(session)
            await db.flush()
        return session
    except IntegrityError:
        existing = (
            await db.execute(select(Session).where(Session.platform_call_id == platform_call_id))
        ).scalar_one()
        existing.run_id = run_id  # fence takeover
        existing.status = SessionStatus.active
        existing.left_at = None
        existing.ended_reason = None
        await db.flush()
        return existing


async def mark_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    status: SessionStatus,
    ended_reason: str | None = None,
) -> None:
    session = await db.get(Session, session_id)
    if session is None:
        return
    session.status = status
    session.ended_reason = ended_reason
    if status in (SessionStatus.ended, SessionStatus.failed):
        session.left_at = datetime.now(UTC)


async def upsert_segment(
    db: AsyncSession,
    *,
    meeting_id: uuid.UUID,
    session_id: uuid.UUID,
    speaker_id: str,
    utterance_id: str,
    text: str,
    language: str | None,
    start_ms: int | float | None,
    end_ms: int | float | None,
) -> uuid.UUID:
    """Persist a final segment with a DB-authoritative, durable meeting_seq.

    The sequence is computed inside the caller's transaction under a per-meeting row lock (so it
    survives a Redis/cache wipe and can't collide across sessions). A late correction of the same
    utterance UPSERTs in place (text/language/timing refreshed, meeting_seq kept, revision bumped).
    """
    # Serialize seq assignment for this meeting.
    await db.execute(select(Meeting.id).where(Meeting.id == meeting_id).with_for_update())
    next_seq = await db.scalar(
        select(func.coalesce(func.max(TranscriptSegment.meeting_seq), 0) + 1)
        .select_from(TranscriptSegment)
        .join(Session, TranscriptSegment.session_id == Session.id)
        .where(Session.meeting_id == meeting_id)
    )
    start_ts = float(start_ms) / 1000.0 if start_ms is not None else None
    end_ts = float(end_ms) / 1000.0 if end_ms is not None else None
    stmt = (
        pg_insert(TranscriptSegment)
        .values(
            session_id=session_id,
            speaker_id=speaker_id,
            utterance_id=utterance_id,
            meeting_seq=next_seq,
            text=text,
            source_language=language,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        .on_conflict_do_update(
            index_elements=["session_id", "speaker_id", "utterance_id"],
            set_={
                "text": text,
                "source_language": language,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "revision": TranscriptSegment.revision + 1,
                # meeting_seq intentionally NOT updated — ordering is fixed at first insert.
            },
        )
        .returning(TranscriptSegment.id)
    )
    return (await db.execute(stmt)).scalar_one()


async def upsert_translation(
    db: AsyncSession,
    *,
    segment_id: uuid.UUID,
    target_language: str,
    text: str,
    source_revision: int = 0,
) -> None:
    """Persist a segment's translation; re-translation of a corrected segment UPSERTs in place."""
    stmt = (
        pg_insert(Translation)
        .values(
            segment_id=segment_id,
            target_language=target_language,
            text=text,
            source_revision=source_revision,
        )
        .on_conflict_do_update(
            index_elements=["segment_id", "target_language"],
            set_={"text": text, "source_revision": source_revision},
        )
    )
    await db.execute(stmt)
