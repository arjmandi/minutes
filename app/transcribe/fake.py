"""Deterministic transcriber for tests and key-less local runs.

Emits one interim + one final segment per audio chunk received, so a test that sends N audio
frames deterministically yields N persisted segments.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.transcribe.base import FinalSegment, Interim, TranscriptEvent


class FakeTranscriber:
    def __init__(
        self,
        *,
        language: str = "en",
        language_hints: list[str] | None = None,
        vocabulary: list[str] | None = None,
    ) -> None:
        self._language = language

    async def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[TranscriptEvent]:
        index = 0
        async for _chunk in audio:
            index += 1
            yield Interim(text=f"interim {index}")
            yield FinalSegment(
                text=f"utterance {index}", utterance_id=f"u{index}", language=self._language
            )
