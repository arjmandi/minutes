"""Build a Translator factory from settings: Claude if a key is set, else the fake."""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.logging import get_logger
from app.translate.base import Translator
from app.translate.claude import ClaudeTranslator
from app.translate.fake import FakeTranslator

log = get_logger("translate")

TranslatorFactory = Callable[[list[str] | None], Translator]


def make_translator_factory(settings: Settings) -> TranslatorFactory:
    use_claude = bool(settings.anthropic_api_key)
    log.info("translate.provider", provider="claude" if use_claude else "fake")

    def factory(vocabulary: list[str] | None = None) -> Translator:
        if use_claude:
            return ClaudeTranslator(
                api_key=settings.anthropic_api_key,
                model=settings.translation_model,
                vocabulary=vocabulary,
            )
        return FakeTranslator(vocabulary=vocabulary)

    return factory
