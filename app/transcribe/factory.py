"""Build a Transcriber factory from settings: real Soniox if a key is set, else the fake."""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.logging import get_logger
from app.transcribe.base import Transcriber
from app.transcribe.fake import FakeTranscriber
from app.transcribe.soniox import SonioxTranscriber

log = get_logger("transcribe")

# A factory takes the per-session custom vocabulary and returns a fresh Transcriber.
TranscriberFactory = Callable[["list[str] | None"], Transcriber]


def make_transcriber_factory(settings: Settings) -> TranscriberFactory:
    use_soniox = bool(settings.soniox_api_key)
    log.info("transcribe.provider", provider="soniox" if use_soniox else "fake")

    def factory(vocabulary: list[str] | None = None) -> Transcriber:
        if use_soniox:
            return SonioxTranscriber(
                api_key=settings.soniox_api_key,
                language_hints=settings.language_hints,
                vocabulary=vocabulary,
            )
        return FakeTranscriber(language_hints=settings.language_hints, vocabulary=vocabulary)

    return factory
