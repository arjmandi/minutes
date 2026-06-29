"""Data model (spec v3 §10).

Invariants enforced at the DB layer:
  - meetings: UNIQUE(platform, external_meeting_id) -> identity races are safe.
  - sessions: UNIQUE(platform_call_id); left_at IS NULL means active.
  - transcript_segments: UNIQUE(session_id, speaker_id, utterance_id) -> UPSERT corrections.
  - translations: UNIQUE(segment_id, target_language), ON DELETE CASCADE from segment.
  - audio_chunks: UNIQUE(session_id, speaker_id, seq); two-phase write via `state`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Platform(enum.StrEnum):
    teams = "teams"
    meet = "meet"
    upload = "upload"  # an uploaded audio file transcribed as a (single-session) meeting
    web = "web"  # generic browser-tab audio capture (any site, not just Meet/Teams)


class SessionStatus(enum.StrEnum):
    joining = "joining"
    active = "active"
    ended = "ended"
    failed = "failed"


class ChunkState(enum.StrEnum):
    pending = "pending"
    recorded = "recorded"
    lost = "lost"


class ConsentStatus(enum.StrEnum):
    pending = "pending"
    granted = "granted"
    denied = "denied"
    withdrawn = "withdrawn"


class TokenKind(enum.StrEnum):
    web_refresh = "web_refresh"  # rotated DB-backed refresh token for the web session
    device = "device"  # long-lived token for the capture extension


class TranslationStatus(enum.StrEnum):
    pending = "pending"  # queued / in flight (on-demand)
    ok = "ok"  # produced successfully
    failed = "failed"  # provider error or empty result; re-translatable on demand


class TranslationSource(enum.StrEnum):
    auto = "auto"  # produced live by the session pipeline
    manual = "manual"  # produced on demand via the read API ("translate this line")


class JobStatus(enum.StrEnum):
    queued = "queued"  # accepted, awaiting a worker
    processing = "processing"  # claimed by a worker (file transcription in flight)
    done = "done"  # transcript persisted
    failed = "failed"  # provider/processing error (see error)
    canceled = "canceled"  # canceled by the owner before completion


def _uuid_col() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        UniqueConstraint("platform", "external_meeting_id", name="uq_meeting_identity"),
    )

    id: Mapped[uuid.UUID] = _uuid_col()
    platform: Mapped[Platform] = mapped_column(SAEnum(Platform, name="platform"), nullable=False)
    external_meeting_id: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str | None] = mapped_column(Text())
    # Owner (claimed by capture-token / consent). NULL = unowned, visible to admins only.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    consent_status: Mapped[ConsentStatus] = mapped_column(
        SAEnum(ConsentStatus, name="consent_status"),
        default=ConsentStatus.pending,
        nullable=False,
    )
    consent_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # First-class per-meeting translation config (spec v3 §7). Seeded from the owner's defaults on
    # claim; editable via the read API. output_language NULL == "never configured" (seed sentinel).
    translation_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    translation_output_language: Mapped[str | None] = mapped_column(String(16))
    translation_input_language: Mapped[str] = mapped_column(
        String(16), default="detect", nullable=False
    )
    translation_prompt: Mapped[str | None] = mapped_column(Text())
    translation_model: Mapped[str | None] = mapped_column(String(128))
    # Public share link: an opaque, unguessable token grants anonymous read-only access. NULL = not
    # shared. Revoking rotates (mint a new token) so previously-shared URLs stop resolving.
    share_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = _created_at()

    sessions: Mapped[list[Session]] = relationship(back_populates="meeting")


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("platform_call_id", name="uq_session_platform_call_id"),)

    id: Mapped[uuid.UUID] = _uuid_col()
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform_call_id: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, name="session_status"),
        default=SessionStatus.joining,
        nullable=False,
        index=True,
    )
    ended_reason: Mapped[str | None] = mapped_column(String(64))
    # Fencing token: the owning worker's run id (spec v3 §13).
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_snapshot: Mapped[dict | None] = mapped_column(JSONB())
    joined_at: Mapped[datetime] = _created_at()
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    meeting: Mapped[Meeting] = relationship(back_populates="sessions")


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("session_id", "speaker_id", name="uq_participant_speaker"),
    )

    id: Mapped[uuid.UUID] = _uuid_col()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    speaker_id: Mapped[str] = mapped_column(String(128), nullable=False, default="mixed")
    display_name: Mapped[str | None] = mapped_column(Text())


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "speaker_id", "utterance_id", name="uq_segment_utterance"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_col()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    speaker_id: Mapped[str] = mapped_column(String(128), nullable=False, default="mixed")
    utterance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Monotonic per meeting -> deterministic merged-timeline ordering across sessions.
    meeting_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    start_ts: Mapped[float | None] = mapped_column()
    end_ts: Mapped[float | None] = mapped_column()
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    source_language: Mapped[str | None] = mapped_column(String(16))

    translations: Mapped[list[Translation]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )


class Translation(Base):
    __tablename__ = "translations"
    __table_args__ = (
        UniqueConstraint("segment_id", "target_language", name="uq_translation_target"),
    )

    id: Mapped[uuid.UUID] = _uuid_col()
    segment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_language: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[TranslationStatus] = mapped_column(
        SAEnum(TranslationStatus, name="translation_status"),
        default=TranslationStatus.ok,
        nullable=False,
    )
    source: Mapped[TranslationSource] = mapped_column(
        SAEnum(TranslationSource, name="translation_source"),
        default=TranslationSource.auto,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    segment: Mapped[TranscriptSegment] = relationship(back_populates="translations")


class AudioChunk(Base):
    __tablename__ = "audio_chunks"
    __table_args__ = (
        UniqueConstraint("session_id", "speaker_id", "seq", name="uq_chunk_seq"),
    )

    id: Mapped[uuid.UUID] = _uuid_col()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    speaker_id: Mapped[str] = mapped_column(String(128), nullable=False, default="mixed")
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[ChunkState] = mapped_column(
        SAEnum(ChunkState, name="chunk_state"), default=ChunkState.pending, nullable=False
    )
    start_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float | None] = mapped_column()
    gap_flag: Mapped[bool] = mapped_column(default=False, nullable=False)


class ConfigChange(Base):
    __tablename__ = "config_changes"

    id: Mapped[uuid.UUID] = _uuid_col()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(String(32), default="session", nullable=False)
    source_language_hints: Mapped[list[str] | None] = mapped_column(ARRAY(String(16)))
    custom_vocabulary: Mapped[dict | None] = mapped_column(JSONB())
    translation_targets: Mapped[list[str] | None] = mapped_column(ARRAY(String(16)))
    actor: Mapped[str | None] = mapped_column(String(256))
    config_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    applied_at: Mapped[datetime] = _created_at()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_user_email"),)

    id: Mapped[uuid.UUID] = _uuid_col()
    email: Mapped[str] = mapped_column(String(320), nullable=False)  # stored lowercased
    password_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    is_admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Per-user provider keys, AES-GCM encrypted at rest (app/crypto.py). NULL = not set.
    soniox_key_enc: Mapped[str | None] = mapped_column(Text())
    anthropic_key_enc: Mapped[str | None] = mapped_column(Text())
    # Translation defaults applied when a meeting starts (per-meeting config can override later).
    default_translation_on: Mapped[bool] = mapped_column(default=False, nullable=False)
    default_output_language: Mapped[str | None] = mapped_column(String(16))
    default_model: Mapped[str | None] = mapped_column(String(128))
    # Bumped on password change / account actions to invalidate all outstanding access JWTs.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class AuthToken(Base):
    """Opaque, DB-backed session tokens (hashed). Access JWTs are stateless and NOT stored here;
    these are the revocable web-refresh + device (extension) tokens."""

    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = _uuid_col()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[TokenKind] = mapped_column(SAEnum(TokenKind, name="token_kind"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class TranscriptionJob(Base):
    """An uploaded audio file queued for async (file-API) transcription into its meeting."""

    __tablename__ = "transcription_jobs"

    id: Mapped[uuid.UUID] = _uuid_col()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text())
    content_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status"),
        default=JobStatus.queued,
        nullable=False,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text())
    run_id: Mapped[str | None] = mapped_column(String(64))  # claiming worker (fencing)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
