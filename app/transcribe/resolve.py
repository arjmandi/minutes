"""Resolve a per-user transcriber factory from the owner's encrypted Soniox key + region.

Mirrors translate/resolve.py: the capturing meeting's owner supplies the Soniox key, and the
region they chose alongside it ("us" | "eu") selects the data-residency endpoints — so each user's
audio is processed in their own region on their own key, with no admin-set server region involved.
No key (or a decrypt failure) → None; the caller falls back to the server/fake factory.
"""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings, soniox_rt_url
from app.crypto import decrypt
from app.db.models import User
from app.logging import get_logger
from app.transcribe.base import Transcriber
from app.transcribe.soniox import SonioxTranscriber

log = get_logger("transcribe")

TranscriberFactory = Callable[["list[str] | None"], Transcriber]


def build_user_transcriber_factory(
    user: User | None, *, settings: Settings
) -> TranscriberFactory | None:
    """A transcriber factory bound to ``user``'s Soniox key + region, or None if unavailable."""
    if user is None or not user.soniox_key_enc:
        return None
    try:
        api_key = decrypt(user.soniox_key_enc, secret=settings.secret_key, aad=str(user.id))
    except Exception as exc:  # noqa: BLE001 — corrupt/rotated key: behave as "no key"
        log.warning("transcribe.key_decrypt_failed", user_id=str(user.id), error=repr(exc))
        return None
    url = soniox_rt_url(user.soniox_region)

    def factory(vocabulary: list[str] | None = None) -> Transcriber:
        return SonioxTranscriber(
            api_key=api_key,
            language_hints=settings.language_hints,
            vocabulary=vocabulary,
            url=url,
        )

    return factory
