"""Async persistence helpers (spec v3 §10-11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AudioChunk,
    AuthToken,
    ChunkState,
    ConfigChange,
    ConsentStatus,
    Meeting,
    Platform,
    Session,
    SessionStatus,
    TokenKind,
    TranscriptSegment,
    Translation,
    TranslationSource,
    TranslationStatus,
    User,
)


async def upsert_meeting(
    db: AsyncSession, *, platform: str, external_meeting_id: str, owner_id: uuid.UUID | None = None
) -> Meeting:
    """DB-arbitrated identity. A DO UPDATE guarantees a row is RETURNING'd in one statement,
    avoiding the lost-update / NoResultFound race of insert-then-select. The first owner sticks:
    an ownerless capture never clears it; an owner-bearing call claims an unowned meeting."""
    insert = pg_insert(Meeting).values(
        platform=Platform(platform), external_meeting_id=external_meeting_id, owner_id=owner_id
    )
    stmt = insert.on_conflict_do_update(
        index_elements=["platform", "external_meeting_id"],
        set_={"owner_id": func.coalesce(Meeting.owner_id, insert.excluded.owner_id)},
    ).returning(Meeting.id)
    meeting_id = (await db.execute(stmt)).scalar_one()
    meeting = await db.get(Meeting, meeting_id)
    assert meeting is not None
    return meeting


async def list_meetings_for_user(
    db: AsyncSession, *, user_id: uuid.UUID, is_admin: bool, limit: int = 100
) -> list[Meeting]:
    """Owner-scoped meeting list: a user sees only their own; an admin sees all."""
    q = select(Meeting).order_by(Meeting.created_at.desc()).limit(limit)
    if not is_admin:
        q = q.where(Meeting.owner_id == user_id)
    return list((await db.execute(q)).scalars())


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
    status: TranslationStatus = TranslationStatus.ok,
    source: TranslationSource = TranslationSource.auto,
) -> None:
    """Persist a segment's translation; re-translation of a corrected segment UPSERTs in place.

    A later success (e.g. on-demand retry) overwrites a prior ``failed`` row; ``updated_at`` bumps
    via ``onupdate`` so the live fan-out + read API reflect the newest attempt.
    """
    insert = pg_insert(Translation).values(
        segment_id=segment_id,
        target_language=target_language,
        text=text,
        source_revision=source_revision,
        status=status,
        source=source,
    )
    set_ = {
        "text": text,
        "source_revision": source_revision,
        "status": status,
        "source": source,
    }
    # Non-destructive: a successful translation always wins (newest success replaces in place), but
    # a failed/empty retry must never clobber an existing good translation — it only lands when the
    # stored row is not already ok (so failure stays first-class until a real translation exists).
    if status == TranslationStatus.ok:
        stmt = insert.on_conflict_do_update(
            index_elements=["segment_id", "target_language"], set_=set_
        )
    else:
        stmt = insert.on_conflict_do_update(
            index_elements=["segment_id", "target_language"],
            set_=set_,
            where=Translation.status != TranslationStatus.ok,
        )
    await db.execute(stmt)


async def seed_meeting_translation(
    db: AsyncSession,
    *,
    meeting_id: uuid.UUID,
    enabled: bool,
    output_language: str | None,
    model: str | None,
) -> None:
    """Seed a meeting's translation config from the owner's defaults — once, on claim.

    No-ops unless the owner has a default output language AND the meeting has never been configured
    (``translation_output_language IS NULL``), so a user's explicit per-meeting choice is never
    clobbered by a later reconnect.
    """
    if not output_language:
        return
    await db.execute(
        sa_update(Meeting)
        .where(Meeting.id == meeting_id, Meeting.translation_output_language.is_(None))
        .values(
            translation_enabled=enabled,
            translation_output_language=output_language,
            translation_model=model,
        )
    )


async def set_meeting_translation_config(
    db: AsyncSession, *, meeting_id: uuid.UUID, **fields: object
) -> None:
    """Update the given translation-config columns on a meeting (caller validates ownership)."""
    allowed = {
        "translation_enabled",
        "translation_output_language",
        "translation_input_language",
        "translation_prompt",
        "translation_model",
    }
    values = {k: v for k, v in fields.items() if k in allowed}
    if not values:
        return
    await db.execute(sa_update(Meeting).where(Meeting.id == meeting_id).values(**values))


async def get_segment_for_translation(
    db: AsyncSession, segment_id: uuid.UUID
) -> tuple[TranscriptSegment, Meeting] | None:
    """Load a final segment + its owning meeting (for on-demand translate + ownership check)."""
    row = (
        await db.execute(
            select(TranscriptSegment, Meeting)
            .join(Session, TranscriptSegment.session_id == Session.id)
            .join(Meeting, Session.meeting_id == Meeting.id)
            .where(TranscriptSegment.id == segment_id)
        )
    ).first()
    return (row[0], row[1]) if row is not None else None


# --- audio archival (two-phase write, spec v3 §9) ---


async def reserve_chunk(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    speaker_id: str,
    s3_key: str,
    seq: int,
    duration_s: float,
) -> uuid.UUID:
    """Phase 1: reserve the (seq, s3_key) row as PENDING before the upload."""
    chunk = AudioChunk(
        session_id=session_id,
        speaker_id=speaker_id,
        s3_key=s3_key,
        seq=seq,
        state=ChunkState.pending,
        duration_s=duration_s,
    )
    db.add(chunk)
    await db.flush()
    return chunk.id


async def next_chunk_seq(db: AsyncSession, *, session_id: uuid.UUID, speaker_id: str) -> int:
    """Monotonic chunk seq for (session, speaker) — survives reconnect (no reset to 0)."""
    value = await db.scalar(
        select(func.coalesce(func.max(AudioChunk.seq), -1) + 1).where(
            AudioChunk.session_id == session_id, AudioChunk.speaker_id == speaker_id
        )
    )
    return int(value or 0)


async def mark_chunk(db: AsyncSession, *, chunk_id: uuid.UUID, state: ChunkState) -> None:
    """Phase 2: mark the chunk RECORDED after a successful upload (or LOST on reconciliation)."""
    chunk = await db.get(AudioChunk, chunk_id)
    if chunk is not None:
        chunk.state = state


# --- read side (transcript view) ---


async def list_recent_meetings(db: AsyncSession, *, limit: int = 100) -> list[Meeting]:
    rows = await db.execute(select(Meeting).order_by(Meeting.created_at.desc()).limit(limit))
    return list(rows.scalars())


async def get_meeting(db: AsyncSession, meeting_id: uuid.UUID) -> Meeting | None:
    return await db.get(Meeting, meeting_id)


async def set_meeting_title(db: AsyncSession, *, meeting_id: uuid.UUID, title: str | None) -> None:
    """Rename a meeting (caller validates ownership)."""
    await db.execute(sa_update(Meeting).where(Meeting.id == meeting_id).values(title=title))


async def transcript_for_meeting(
    db: AsyncSession, meeting_id: uuid.UUID, *, after_seq: int = 0, limit: int = 500
) -> list[tuple[TranscriptSegment, datetime]]:
    """Final segments across the meeting's sessions, ordered by meeting_seq, translations eager.

    Each row is ``(segment, session_joined_at)`` — the session's join time is the media epoch, so
    absolute wall-clock timing is ``joined_at + start_ts`` (correct even across reconnect sessions,
    where each session's relative ``start_ts`` resets to 0).

    Paged: returns at most ``limit`` segments with meeting_seq > after_seq; the caller pages by the
    max returned meeting_seq.
    """
    rows = await db.execute(
        select(TranscriptSegment, Session.joined_at)
        .join(Session, TranscriptSegment.session_id == Session.id)
        .where(Session.meeting_id == meeting_id, TranscriptSegment.meeting_seq > after_seq)
        .order_by(TranscriptSegment.meeting_seq)
        .limit(limit)
        .options(selectinload(TranscriptSegment.translations))
    )
    return [(seg, joined_at) for seg, joined_at in rows.all()]


# --- control plane (spec v3 §8) ---


async def get_session_by_call_id(db: AsyncSession, call_id: str) -> Session | None:
    return (
        await db.execute(select(Session).where(Session.platform_call_id == call_id))
    ).scalar_one_or_none()


async def insert_config_change(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    scope: str,
    source_language_hints: list[str] | None,
    custom_vocabulary: list[str] | None,
    translation_targets: list[str] | None,
    actor: str,
) -> int:
    """Append an audited config change; returns the new monotonic config_generation."""
    # Serialize generation assignment for this session (mirrors upsert_segment's per-row lock) so
    # concurrent set_config cannot read the same MAX and emit a duplicate generation / lose a write.
    await db.execute(select(Session.id).where(Session.id == session_id).with_for_update())
    generation = await db.scalar(
        select(func.coalesce(func.max(ConfigChange.config_generation), 0) + 1).where(
            ConfigChange.session_id == session_id
        )
    )
    vocab = {"terms": custom_vocabulary} if custom_vocabulary is not None else None
    db.add(
        ConfigChange(
            session_id=session_id,
            scope=scope,
            source_language_hints=source_language_hints,
            custom_vocabulary=vocab,
            translation_targets=translation_targets,
            actor=actor,
            config_generation=generation,
        )
    )
    await db.flush()
    return int(generation)


async def latest_config_state(
    db: AsyncSession, *, session_id: uuid.UUID
) -> tuple[int, list[str] | None]:
    """Control-plane catch-up: (max config_generation, latest non-null translation_targets).

    Lets the owning worker converge to the most recent audited config on subscribe — closing the
    subscribe-race window and letting a worker that takes over a reconnecting session (run_id fence)
    inherit prior config.
    """
    max_gen = await db.scalar(
        select(func.coalesce(func.max(ConfigChange.config_generation), 0)).where(
            ConfigChange.session_id == session_id
        )
    )
    targets = await db.scalar(
        select(ConfigChange.translation_targets)
        .where(
            ConfigChange.session_id == session_id,
            ConfigChange.translation_targets.is_not(None),
        )
        .order_by(ConfigChange.config_generation.desc())
        .limit(1)
    )
    return int(max_gen or 0), (list(targets) if targets is not None else None)


# --- GDPR: consent, erasure, retention (spec v3 §15) ---


async def get_meeting_by_identity(
    db: AsyncSession, *, platform: str, external_meeting_id: str
) -> Meeting | None:
    return (
        await db.execute(
            select(Meeting).where(
                Meeting.platform == Platform(platform),
                Meeting.external_meeting_id == external_meeting_id,
            )
        )
    ).scalar_one_or_none()


async def set_consent(
    db: AsyncSession,
    *,
    platform: str,
    external_meeting_id: str,
    status: ConsentStatus,
    owner_id: uuid.UUID | None = None,
) -> Meeting:
    meeting = await upsert_meeting(
        db, platform=platform, external_meeting_id=external_meeting_id, owner_id=owner_id
    )
    meeting.consent_status = status
    meeting.consent_captured_at = datetime.now(UTC)
    await db.flush()
    return meeting


async def chunk_keys_for_meeting(db: AsyncSession, meeting_id: uuid.UUID) -> list[str]:
    rows = await db.execute(
        select(AudioChunk.s3_key)
        .join(Session, AudioChunk.session_id == Session.id)
        .where(Session.meeting_id == meeting_id)
    )
    return [r[0] for r in rows.all()]


async def delete_meeting(db: AsyncSession, meeting_id: uuid.UUID) -> bool:
    """Delete a meeting; DB FK cascade removes its sessions/segments/translations/chunks."""
    res = await db.execute(sa_delete(Meeting).where(Meeting.id == meeting_id))
    return res.rowcount > 0


# --- user accounts + session/device tokens (Chunk 2) ---


async def create_user(
    db: AsyncSession, *, email: str, password_hash: str, is_admin: bool = False
) -> User:
    user = User(email=email.lower(), password_hash=password_hash, is_admin=is_admin)
    db.add(user)
    await db.flush()
    return user


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def count_users(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(User)) or 0)


async def list_users(db: AsyncSession) -> list[User]:
    return list((await db.execute(select(User).order_by(User.created_at))).scalars())


async def delete_user(db: AsyncSession, user_id: uuid.UUID) -> bool:
    res = await db.execute(sa_delete(User).where(User.id == user_id))
    return res.rowcount > 0


async def create_auth_token(
    db: AsyncSession, *, user_id: uuid.UUID, kind: TokenKind, token_hash: str, expires_at: datetime
) -> AuthToken:
    tok = AuthToken(user_id=user_id, kind=kind, token_hash=token_hash, expires_at=expires_at)
    db.add(tok)
    await db.flush()
    return tok


async def get_active_auth_token(
    db: AsyncSession, *, token_hash: str, kind: TokenKind
) -> AuthToken | None:
    return (
        await db.execute(
            select(AuthToken).where(
                AuthToken.token_hash == token_hash,
                AuthToken.kind == kind,
                AuthToken.revoked_at.is_(None),
                AuthToken.expires_at > datetime.now(UTC),
            )
        )
    ).scalar_one_or_none()


async def get_auth_token_any(db: AsyncSession, *, token_hash: str) -> AuthToken | None:
    """Look up a token by hash regardless of revoked/expired state (for refresh reuse-detection)."""
    return (
        await db.execute(select(AuthToken).where(AuthToken.token_hash == token_hash))
    ).scalar_one_or_none()


async def revoke_auth_token(db: AsyncSession, *, token_hash: str) -> None:
    tok = (
        await db.execute(select(AuthToken).where(AuthToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if tok is not None and tok.revoked_at is None:
        tok.revoked_at = datetime.now(UTC)


async def touch_auth_token(db: AsyncSession, token: AuthToken) -> None:
    token.last_used_at = datetime.now(UTC)


async def revoke_user_tokens(
    db: AsyncSession, *, user_id: uuid.UUID, kind: TokenKind | None = None
) -> None:
    """Revoke a user's active tokens (all, or one kind) — e.g. on password change / logout-all."""
    q = select(AuthToken).where(AuthToken.user_id == user_id, AuthToken.revoked_at.is_(None))
    if kind is not None:
        q = q.where(AuthToken.kind == kind)
    now = datetime.now(UTC)
    for tok in (await db.execute(q)).scalars():
        tok.revoked_at = now


async def expired_meeting_ids(
    db: AsyncSession, *, before: datetime, limit: int = 500
) -> list[uuid.UUID]:
    rows = await db.execute(select(Meeting.id).where(Meeting.created_at < before).limit(limit))
    return [r[0] for r in rows.all()]


async def pending_chunks_for_ended_sessions(
    db: AsyncSession, *, limit: int = 500
) -> list[tuple[uuid.UUID, str]]:
    """PENDING chunks whose session has ended/failed — their upload is over, so a still-PENDING
    row is an orphan to reconcile (RECORDED if the object exists, else LOST)."""
    rows = await db.execute(
        select(AudioChunk.id, AudioChunk.s3_key)
        .join(Session, AudioChunk.session_id == Session.id)
        .where(
            AudioChunk.state == ChunkState.pending,
            Session.status.in_([SessionStatus.ended, SessionStatus.failed]),
        )
        .limit(limit)
    )
    return [(r[0], r[1]) for r in rows.all()]
