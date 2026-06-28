"""Auth dependencies/helpers for HTTP routes and the ingest WebSocket."""

from __future__ import annotations

import uuid

import jwt
from fastapi import Depends, Header, HTTPException, Request, WebSocket

from app.auth.sessions import hash_token, verify_access_token
from app.auth.tokens import AuthError, Claims, verify_capability_token
from app.db import repo
from app.db.models import TokenKind, User
from app.logging import get_logger

log = get_logger("auth")

# Web session cookies (set by /api/auth/login). HttpOnly; Secure in non-dev (see accounts.py).
SESSION_COOKIE = "mn_session"
REFRESH_COOKIE = "mn_refresh"

# Preferred WS token transport: client offers two subprotocols, [AUTH_SUBPROTOCOL, <token>].
# Browsers can set subprotocols (new WebSocket(url, protocols)) but not headers, and unlike a
# ?token= query param the token does not land in access logs / proxies / history.
AUTH_SUBPROTOCOL = "minutes.auth.bearer"


def _bearer(value: str | None) -> str | None:
    if value and value.lower().startswith("bearer "):
        return value[7:].strip()
    return None


async def require_claims(
    request: Request, authorization: str | None = Header(default=None)
) -> Claims:
    """HTTP dependency: 401 unless a valid capability token is presented (for control routes)."""
    settings = request.app.state.settings
    try:
        return verify_capability_token(
            _bearer(authorization) or "",
            secret=settings.auth_secret,
            algorithm=settings.auth_algorithm,
        )
    except AuthError as exc:
        log.info("auth.http_rejected", error=str(exc))
        raise HTTPException(status_code=401, detail="unauthorized") from exc  # generic to client


def ws_token(ws: WebSocket) -> tuple[str | None, str | None]:
    """Extract the WS capability token. Returns ``(token, subprotocol_to_echo)``.

    Order: ``Sec-WebSocket-Protocol`` (preferred; must be echoed on accept), then ``?token=``
    (fallback — leaks via URL, kept only for non-browser clients), then Authorization header.
    """
    proto = ws.headers.get("sec-websocket-protocol")
    if proto:
        parts = [p.strip() for p in proto.split(",")]
        if len(parts) >= 2 and parts[0] == AUTH_SUBPROTOCOL and parts[1]:
            return parts[1], AUTH_SUBPROTOCOL
    query_token = ws.query_params.get("token")
    if query_token:
        return query_token, None
    return _bearer(ws.headers.get("authorization")), None


# --- user-account auth (Chunk 2): web session cookie + extension device token ---


async def require_user(request: Request) -> User:
    """HTTP dependency: the current web user from the session-cookie access JWT, else 401."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    settings = request.app.state.settings
    try:
        payload = verify_access_token(
            token, secret=settings.auth_secret, algorithm=settings.auth_algorithm
        )
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="invalid session") from exc
    async with request.app.state.session_factory() as db:
        user = await repo.get_user_by_id(db, user_id)
    # token_version mismatch => the JWT predates a password change / eviction => reject.
    if user is None or not user.is_active or payload.get("ver") != user.token_version:
        raise HTTPException(status_code=401, detail="no such user")
    return user


async def ws_user(ws: WebSocket) -> User | None:
    """WebSocket equivalent of require_user (session cookie). Returns None on any failure; the
    caller closes the socket. Used by the live transcript WS for the signed-in web app."""
    token = ws.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    settings = ws.app.state.settings
    try:
        payload = verify_access_token(
            token, secret=settings.auth_secret, algorithm=settings.auth_algorithm
        )
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        return None
    async with ws.app.state.session_factory() as db:
        user = await repo.get_user_by_id(db, user_id)
    if user is None or not user.is_active or payload.get("ver") != user.token_version:
        return None
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin required")
    return user


async def require_device(
    request: Request, authorization: str | None = Header(default=None)
) -> User:
    """HTTP dependency for the capture extension: a valid device token (Bearer) -> its user."""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    async with request.app.state.session_factory() as db:
        tok = await repo.get_active_auth_token(
            db, token_hash=hash_token(token), kind=TokenKind.device
        )
        if tok is None:
            raise HTTPException(status_code=401, detail="invalid device token")
        user = await repo.get_user_by_id(db, tok.user_id)
        await repo.touch_auth_token(db, tok)
        await db.commit()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="no such user")
    return user
