"""Resolve a per-user / per-meeting translator from the owner's encrypted Anthropic key.

Translation is first-class per meeting (spec v3 §7): the meeting carries the config (enabled,
output language, model, prompt) and the owning user supplies the LLM key. We build the Claude
translator from the *owner's* decrypted key — never a server-wide key — so each user's usage and
billing stay their own. No key (or decrypt failure) → no Claude translator; the caller decides
whether to fall back to the key-less fake (live capture) or surface a "no key" state (on demand).
"""

from __future__ import annotations

from app.config import Settings
from app.crypto import decrypt
from app.db.models import User
from app.logging import get_logger
from app.translate.base import Translator
from app.translate.claude import ClaudeTranslator

log = get_logger("translate")


def build_user_translator(
    user: User | None,
    *,
    settings: Settings,
    model: str | None = None,
    vocabulary: list[str] | None = None,
) -> Translator | None:
    """Claude translator bound to ``user``'s Anthropic key, or None if unavailable."""
    if user is None or not user.anthropic_key_enc:
        return None
    try:
        api_key = decrypt(user.anthropic_key_enc, secret=settings.secret_key, aad=str(user.id))
    except Exception as exc:  # noqa: BLE001 — corrupt/rotated key: behave as "no key"
        log.warning("translate.key_decrypt_failed", user_id=str(user.id), error=repr(exc))
        return None
    return ClaudeTranslator(
        api_key=api_key,
        model=model or settings.translation_model,
        vocabulary=vocabulary,
    )
