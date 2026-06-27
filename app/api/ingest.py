"""Capture-client ingest WebSocket (authenticated; drives the transcription pipeline).

Auth: capability token via the ``minutes.auth.bearer`` subprotocol (preferred), ``?token=``, or
Authorization header; rejected at the handshake (close 1008) before accept. The token's
``meetings`` scope authorizes the meeting (per-session authorization).

Protocol: JSON ``hello`` ({type, platform, external_meeting_id, call_id}) -> ``admitted`` /
``rejected`` / ``conflict`` / ``forbidden``. Then binary frames (encoded PCM, see app.audio.frames)
are decoded and fed to the per-call Session Manager; a ``{"type":"end"}`` text frame finalizes
gracefully and the server replies ``{"type":"ended", session_id, segments}`` once persistence
completes. Any frame renews the admission lease (throttled); a silent client is reaped.

Robustness: the cleanup path is non-blocking and bounded — the admission slot is always released
and the socket always closed even if the pipeline wedges or errors.
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.admission.registry import AcquireResult
from app.audio.frames import decode_frame
from app.auth.dependencies import ws_token
from app.auth.tokens import AuthError, authorize_meeting, verify_capability_token
from app.logging import get_logger
from app.session.adapter import ClientCaptureAdapter
from app.session.events import EndReason
from app.session.manager import SessionManager

router = APIRouter(tags=["ingest"])
log = get_logger("ingest")

_CLOSE_POLICY = 1008  # unauthorized / forbidden / conflict
_CLOSE_PROTOCOL = 1003  # malformed hello
_CLOSE_OVERLOAD = 1013  # at capacity
_CLOSE_INTERNAL = 1011  # unexpected backend error
_FEED_TIMEOUT_S = 2.0


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

    await ws.accept(subprotocol=subprotocol)
    registry = ws.app.state.registry
    owner = registry.mint_owner()
    call_id: str | None = None
    adapter: ClientCaptureAdapter | None = None
    run_task: asyncio.Task | None = None
    graceful = False
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
        adapter = ClientCaptureAdapter(platform, external_meeting_id, call_id)
        manager = SessionManager(
            session_factory=ws.app.state.session_factory,
            redis=ws.app.state.redis,
            transcriber_factory=ws.app.state.transcriber_factory,
            worker_id=registry.worker_id,
            finalize_timeout_s=settings.finalize_timeout_s,
        )
        run_task = asyncio.create_task(manager.run(adapter))
        await ws.send_json(
            {"type": "admitted", "call_id": call_id, "worker_id": registry.worker_id}
        )
        log.info("ingest.admitted", call_id=call_id, platform=platform, principal=claims.principal)

        idle_limit = max(settings.lease_ttl_s, 1)
        last_renew = time.monotonic()
        while True:
            if run_task.done():  # pipeline ended/failed -> stop feeding
                break
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=idle_limit)
            except TimeoutError:
                log.info("ingest.idle_timeout", call_id=call_id)
                break
            if msg["type"] == "websocket.disconnect":
                break

            data = msg.get("bytes")
            if data is not None:
                try:
                    frame = decode_frame(data)
                except ValueError as exc:
                    log.warning("ingest.bad_frame", call_id=call_id, error=str(exc))
                else:
                    try:
                        await asyncio.wait_for(adapter.feed(frame), timeout=_FEED_TIMEOUT_S)
                        frames += 1
                    except TimeoutError:
                        if run_task.done():
                            break  # consumer gone; stop
            else:
                text = msg.get("text")
                try:
                    control = json.loads(text) if text else {}
                except ValueError:
                    control = {}
                if isinstance(control, dict) and control.get("type") == "end":
                    graceful = True
                    break

            now = time.monotonic()
            if now - last_renew >= settings.heartbeat_s:
                if not await registry.renew(call_id, owner):
                    log.warning("ingest.lease_lost", call_id=call_id)
                    break
                last_renew = now
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 — never let a backend error hang the socket
        log.error("ingest.error", call_id=call_id, error=repr(exc))
        graceful = False
    finally:
        summary = None
        if adapter is not None:
            await adapter.end(EndReason.normal if graceful else EndReason.client_lost)
        if run_task is not None:
            try:
                summary = await asyncio.wait_for(run_task, timeout=settings.finalize_timeout_s + 5)
            except TimeoutError:
                log.error("ingest.pipeline_timeout", call_id=call_id)
                await asyncio.gather(run_task, return_exceptions=True)
            except Exception as exc:  # noqa: BLE001
                log.error("ingest.pipeline_failed", call_id=call_id, error=repr(exc))
        if graceful and summary is not None:
            try:
                await ws.send_json(
                    {
                        "type": "ended",
                        "session_id": summary.session_id,
                        "segments": summary.segments,
                    }
                )
            except Exception:  # noqa: BLE001
                pass
        await _safe_close(ws, code=1000 if graceful else _CLOSE_INTERNAL)
        if call_id:
            try:
                await registry.release(call_id, owner)
            except Exception as exc:  # noqa: BLE001
                log.warning("ingest.release_failed", call_id=call_id, error=repr(exc))
            log.info("ingest.closed", call_id=call_id, frames=frames)


async def _safe_close(ws: WebSocket, code: int = 1000) -> None:
    try:
        await ws.close(code=code)
    except RuntimeError:
        pass  # already closed / not in a closable state
