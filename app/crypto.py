"""AES-256-GCM encryption at rest for per-user secrets (the provider API keys).

The 32-byte key is derived from ``MINUTES_SECRET_KEY`` via SHA-256, so the configured value can be
any sufficiently-long secret. ``aad`` binds a ciphertext to a context (the owning user id), so a
row's ciphertext cannot be transplanted onto another row. Rotating ``MINUTES_SECRET_KEY``
invalidates all stored ciphertexts — users re-enter their keys (an accepted v1 trade-off).
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


def _key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt(plaintext: str, *, secret: str, aad: str = "") -> str:
    """Return base64(nonce || ciphertext+tag)."""
    nonce = os.urandom(_NONCE_BYTES)
    ct = AESGCM(_key(secret)).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8") or None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt(token: str, *, secret: str, aad: str = "") -> str:
    raw = base64.b64decode(token)
    nonce, ct = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    return AESGCM(_key(secret)).decrypt(nonce, ct, aad.encode("utf-8") or None).decode("utf-8")
