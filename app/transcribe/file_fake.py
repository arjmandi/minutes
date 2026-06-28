"""Deterministic file transcriber for tests / key-less local runs (no network)."""

from __future__ import annotations

from app.transcribe.file_base import FileSegment


class FakeFileTranscriber:
    def __init__(self, *, language_hints: list[str] | None = None) -> None:
        self._language = (language_hints or ["en"])[0]

    async def transcribe(
        self, audio: bytes, *, language_hints: list[str] | None = None
    ) -> list[FileSegment]:
        if not audio:
            return []
        # Deterministic: one segment per 16 bytes (1..5) with 1s-per-segment timing.
        count = max(1, min(len(audio) // 16, 5))
        lang = (language_hints or [self._language])[0]
        return [
            FileSegment(
                text=f"uploaded utterance {i + 1}",
                start_ms=i * 1000,
                end_ms=i * 1000 + 800,
                language=lang,
            )
            for i in range(count)
        ]
