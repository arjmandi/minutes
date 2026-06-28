"""Web/device session auth: a short-lived signed access JWT + opaque, DB-backed session tokens.

The access token is a stateless HS256 JWT (scope ``session``) carried in an HttpOnly cookie. The
refresh token (web) and device token (extension) are opaque random strings stored *hashed* in
``auth_tokens`` so they are revocable. Capability tokens (app/auth/tokens.py) are unchanged and are
minted server-side for the capture WebSocket once a user/device is authenticated.
"""

from __future__ import annotations

import hashlib
import secrets
import time

import jwt

SESSION_SCOPE = "session"


def issue_access_token(
    *,
    user_id: str,
    is_admin: bool,
    token_version: int,
    secret: str,
    algorithm: str = "HS256",
    ttl_s: int,
    now: int | None = None,
) -> str:
    issued = int(time.time()) if now is None else now
    payload = {
        "sub": user_id,
        "scope": SESSION_SCOPE,
        "admin": is_admin,
        "ver": token_version,  # must match User.token_version, else the JWT is stale (revoked)
        "iat": issued,
        "exp": issued + ttl_s,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def verify_access_token(token: str, *, secret: str, algorithm: str = "HS256") -> dict:
    payload = jwt.decode(
        token,
        secret,
        algorithms=[algorithm],
        options={"require": ["exp", "iat", "sub"], "verify_exp": True},
    )
    if payload.get("scope") != SESSION_SCOPE:
        raise jwt.InvalidTokenError("not a session token")
    return payload


def new_opaque_token() -> str:
    """A high-entropy opaque token (the value handed to the client; only its hash is stored)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
