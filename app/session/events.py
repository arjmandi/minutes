"""Normalized MeetingSession events (spec v3 §5).

Every capture adapter (the v1 ClientCaptureAdapter, or any future server-side adapter)
emits this same event stream, so STT/translation/chunking/persistence/fan-out are
capture-agnostic. On the client path, ``SessionStarted`` always precedes any ``AudioFrame``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

MIXED_SPEAKER = "mixed"


class EndReason(enum.StrEnum):
    normal = "normal"
    kicked = "kicked"
    media_lost = "media_lost"
    join_failed = "join_failed"
    client_lost = "client_lost"
    error = "error"


@dataclass(slots=True)
class SessionStarted:
    platform: str
    external_meeting_id: str
    call_id: str


@dataclass(slots=True)
class ParticipantJoined:
    speaker_id: str
    display_name: str | None = None


@dataclass(slots=True)
class ParticipantLeft:
    speaker_id: str


@dataclass(slots=True)
class AudioFrame:
    pcm: bytes
    timestamp: float  # seconds, derived from cumulative sample count on the client
    speaker_id: str = MIXED_SPEAKER


@dataclass(slots=True)
class SessionEnded:
    reason: EndReason


Event = SessionStarted | ParticipantJoined | ParticipantLeft | AudioFrame | SessionEnded
