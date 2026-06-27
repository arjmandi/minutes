"""Translator abstraction: a finalized transcript segment in, translations out (spec v3 §7).

Runs downstream of finalized segments — never inside the STT path. Provider is pluggable
(``ClaudeTranslator`` / ``FakeTranslator``); selection is configuration, not architecture.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Translator(Protocol):
    async def translate(
        self,
        text: str,
        *,
        source_language: str | None,
        target_languages: list[str],
        vocabulary: list[str] | None = None,
    ) -> dict[str, str]:
        """Return ``{target_language: translated_text}`` for each requested target.

        Implementations skip targets equal to the source language and return only the targets
        they successfully produced (best-effort; translation lag/failure must not block STT).
        """
        ...
