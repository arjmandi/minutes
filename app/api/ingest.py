"""Capture-client ingest WebSocket (authenticated; vertical slice over admission).

Auth: the client presents a capability token (preferably via the ``minutes.auth.bearer``
subprotocol; ``?token=`` and Authorization header are fallbacks). An invalid/missing token is
rejected at the handshake (close 1008) before accept. The token's ``meetings`` scope authorizes
the specific meeting (per-session authorization).

Protocol: the client sends a JSON ``hello`` ({type, platform, external_meeting_id, call_id});
the backend admits against the distributed cap and replies ``admitted``/``rejected``/``conflict``/
``forbidden``, then closes with a distinct code on every denial. Binary frames are PCM (dropped
for now); any frame renews the lease (throttled). A silent client is reaped after a lease period.

TODO(step 3+): on admit, upsert meeting/session rows (owner = principal/run_id), start Soniox +
the chunker, and feed decoded PCM into the ClientCaptureAdapter -> Session Manager.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.admission.registry import AcquireResult
from app.auth.dependencies import ws_token
from app.auth.tokens import AuthError, authorize_meeting, verify_capability_token
from app.logging import get_logger
from app.session.adapter import ClientCaptureAdapter

router = APIRouter(tags=["ingest"])
log = get_logger("ingest")

# WS close codes
_CLOSE_POLICY = 1008  # unauthorized / forbidden / conflict
_CLOSE_PROTOCOL = 1003  # malformed hello
_CLOSE_OVERLOAD = 1013  # at capacity (try again later)
_CLOSE_INTERNAL = 1011  # unexpected backend error


@router.websocket("/ingest")
async def ingest_ws(ws: WebSocket) -> None:
    settings = ws.app.state.settings
    token, subprotocol = ws_token(ws)
    try:
        claims = verify_capability_token(
            token or "", secret=settings.auth_secret, algorithm=settings.auth_algorithm
        )
    except AuthError as exc:
        await ws.close(code=_CLOSE_POLICY, reason="unauthorized")
        log.info("ingest.unauthorized", error=str(exc))
        return

    # If the client offered the auth subprotocol, we must echo it on accept.
    await ws.accept(subprotocol=subprotocol)
    registry = ws.app.state.registry
    owner = registry.mint_owner()
    call_id: str | None = None
    frames = 0
    try:
        try:
            hello = await ws.receive_json()
        except (KeyError, ValueError):
            await ws.close(code=_CLOSE_PROTOCOL, reason="invalid_hello")
            return
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            await ws.send_json({"type": "error", "reason": "expected_hello"})
            await ws.close(code=_CLOSE_PROTOCOL)
            return

        platform = hello.get("platform")
        external_meeting_id = hello.get("external_meeting_id")
        requested_call_id = hello.get("call_id")
        if not (
            isinstance(platform, str)
            and isinstance(external_meeting_id, str)
            and isinstance(requested_call_id, str)
        ):
            await ws.send_json({"type": "error", "reason": "missing_fields"})
            await ws.close(code=_CLOSE_PROTOCOL)
            return

        if not authorize_meeting(claims, external_meeting_id):
            await ws.send_json({"type": "forbidden", "reason": "meeting_not_authorized"})
            await ws.close(code=_CLOSE_POLICY)
            return

        result = await registry.acquire(requested_call_id, owner)
        if result is AcquireResult.AT_CAPACITY:
            await ws.send_json({"type": "rejected", "reason": "at_capacity"})
            await ws.close(code=_CLOSE_OVERLOAD)
            return
        if result is AcquireResult.CONFLICT:
            await ws.send_json({"type": "conflict", "reason": "already_active"})
            await ws.close(code=_CLOSE_POLICY)
            return

        call_id = requested_call_id  # we now own a slot -> release in finally
        ClientCaptureAdapter(platform, external_meeting_id, call_id)
        await ws.send_json(
            {"type": "admitted", "call_id": call_id, "worker_id": registry.worker_id}
        )
        log.info("ingest.admitted", call_id=call_id, platform=platform, principal=claims.principal)

        idle_limit = max(settings.lease_ttl_s, 1)
        last_renew = time.monotonic()
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=idle_limit)
            except TimeoutError:
                log.info("ingest.idle_timeout", call_id=call_id)
                break
            if msg["type"] == "websocket.disconnect":
                break
            # Any frame is liveness; renew at most every heartbeat to avoid hammering Redis.
            now = time.monotonic()
            if now - last_renew >= settings.heartbeat_s:
                if not await registry.renew(call_id, owner):
                    log.warning("ingest.lease_lost", call_id=call_id)
                    break
                last_renew = now
            if msg.get("bytes") is not None:
                frames += 1  # TODO(step 3): decode + feed Session Manager
        await _safe_close(ws)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 — never let a backend error hang the socket
        log.error("ingest.error", call_id=call_id, error=repr(exc))
        await _safe_close(ws, code=_CLOSE_INTERNAL)
    finally:
        if call_id:
            try:
                await registry.release(call_id, owner)
            except Exception as exc:  # noqa: BLE001 — best-effort; don't mask the close
                log.warning("ingest.release_failed", call_id=call_id, error=repr(exc))
            log.info("ingest.closed", call_id=call_id, frames=frames)


async def _safe_close(ws: WebSocket, code: int = 1000) -> None:
    try:
        await ws.close(code=code)
    except RuntimeError:
        pass  # already closed / not in a closable state
