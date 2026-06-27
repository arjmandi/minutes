"""Auth dependencies/helpers for HTTP routes and the ingest WebSocket."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request, WebSocket

from app.auth.tokens import AuthError, Claims, verify_capability_token
from app.logging import get_logger

log = get_logger("auth")

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
