"""File (async/batch) transcription abstraction — distinct from the real-time WS transcriber.

An uploaded audio file is transcribed in one shot via a provider's async file API (Soniox) and
mapped to ordered segments. Provider is pluggable (``SonioxFileTranscriber`` / the fake); selection
is configuration, not architecture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FileSegment:
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    language: str | None = None
    speaker_id: str = "mixed"


@runtime_checkable
class FileTranscriber(Protocol):
    async def transcribe(
        self, audio: bytes, *, language_hints: list[str] | None = None
    ) -> list[FileSegment]:
        """Transcribe a whole audio file into ordered segments. Raises on unrecoverable failure."""
        ...
