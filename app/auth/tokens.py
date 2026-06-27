"""Capability tokens for the auth edge (spec v3 §15).

v1 authn is a signed capability token: the operator running a capture client presents a token
that authenticates a *principal* and authorizes capture of a set of meetings. This is the
documented v1 mechanism; it leaves a clean seam for full user management later (swap the issuer
for a real IdP; verification stays the same).

The token is a JWT (HS256 by default) with claims: ``sub`` (principal), ``scope`` ("capture"),
``meetings`` (allowed external_meeting_ids, or ``["*"]``), ``iat``, ``exp``, ``jti``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import jwt

CAPTURE_SCOPE = "capture"


class AuthError(Exception):
    """Raised when a token is missing, malformed, expired, tampered, or wrongly scoped."""


@dataclass(slots=True)
class Claims:
    principal: str
    meetings: list[str]
    raw: dict
    admin: bool = False  # required for destructive ops (erasure); not granted to capture tokens


def issue_capability_token(
    *,
    principal: str,
    secret: str,
    algorithm: str = "HS256",
    ttl_s: int = 3600,
    meetings: list[str] | None = None,
    admin: bool = False,
    now: int | None = None,
) -> str:
    issued = int(time.time()) if now is None else now
    payload = {
        "sub": principal,
        "scope": CAPTURE_SCOPE,
        "meetings": meetings if meetings is not None else ["*"],
        "admin": admin,
        "iat": issued,
        "exp": issued + ttl_s,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def verify_capability_token(token: str, *, secret: str, algorithm: str = "HS256") -> Claims:
    if not token:
        raise AuthError("missing token")
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            options={"require": ["exp", "iat", "sub"], "verify_exp": True},
        )
    except jwt.PyJWTError as exc:  # expired, tampered, wrong secret, malformed, missing claim
        raise AuthError(str(exc)) from exc
    if payload.get("scope") != CAPTURE_SCOPE:
        raise AuthError("token is not scoped for capture")
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("invalid subject")
    meetings = payload.get("meetings")
    # Fail closed: a malformed/absent claim authorizes nothing (no implicit wildcard).
    if not isinstance(meetings, list) or not all(isinstance(m, str) for m in meetings):
        raise AuthError("invalid meetings claim")
    return Claims(principal=sub, meetings=meetings, raw=payload, admin=bool(payload.get("admin")))


def authorize_meeting(claims: Claims, platform: str, external_meeting_id: str) -> bool:
    """Per-session authorization. Scopes are "platform:external_meeting_id" (or "*"), matching the
    DB identity UNIQUE(platform, external_meeting_id) so an id reused across platforms can't be
    cross-authorized."""
    return "*" in claims.meetings or f"{platform}:{external_meeting_id}" in claims.meetings
