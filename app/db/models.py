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
    consent_status: Mapped[ConsentStatus] = mapped_column(
        SAEnum(ConsentStatus, name="consent_status"),
        default=ConsentStatus.pending,
        nullable=False,
    )
    consent_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
