"""Deterministic translator for tests and key-less local runs."""

from __future__ import annotations


class FakeTranslator:
    def __init__(self, *, vocabulary: list[str] | None = None) -> None:
        self._vocabulary = vocabulary or []

    async def translate(
        self,
        text: str,
        *,
        source_language: str | None,
        target_languages: list[str],
        vocabulary: list[str] | None = None,
    ) -> dict[str, str]:
        if not text.strip():
            return {}
        return {t: f"[{t}] {text}" for t in target_languages if t != source_language}
