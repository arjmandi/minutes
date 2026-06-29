"""Build a FileTranscriber from a Soniox key: real async client if a key is set, else the fake.

For uploads the key is the meeting OWNER's Soniox key (decrypted per job); the server-wide key in
settings is only a fallback for dev/local. The factory takes the resolved key so the worker can pass
the owner's key per job.
"""

from __future__ import annotations

from app.logging import get_logger
from app.transcribe.file_base import FileTranscriber
from app.transcribe.file_fake import FakeFileTranscriber
from app.transcribe.file_soniox import SonioxFileTranscriber

log = get_logger("transcribe")


def make_file_transcriber(
    *, api_key: str | None, language_hints: list[str] | None = None, base_url: str | None = None
) -> FileTranscriber:
    if api_key:
        kwargs = {"api_key": api_key, "language_hints": language_hints}
        if base_url:
            kwargs["base_url"] = base_url  # region (EU/US) base from settings.soniox_file_base
        return SonioxFileTranscriber(**kwargs)
    return FakeFileTranscriber(language_hints=language_hints)
