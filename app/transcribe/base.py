"""Transcriber abstraction: a stream of audio bytes in, transcript events out.

Decouples the Session Manager from the STT provider. ``FakeTranscriber`` (deterministic) and
``SonioxTranscriber`` (real WS client) both implement this. Interim events are live-only;
``FinalSegment`` events are the durable record the Session Manager persists.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class Interim:
    text: str


@dataclass(slots=True)
class FinalSegment:
    text: str
    # Stable per-connection id so a later correction UPSERTs in place (spec v3 §6).
    utterance_id: str
    language: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None


TranscriptEvent = Interim | FinalSegment


@runtime_checkable
class Transcriber(Protocol):
    def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[TranscriptEvent]:
        """Consume PCM chunks (16 kHz mono s16le) and yield transcript events until audio ends."""
        ...
