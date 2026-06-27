"""Capture-client ingest WebSocket (vertical slice over admission).

Protocol (skeleton): the client sends a JSON ``hello`` ({type, platform, external_meeting_id,
call_id}); the backend admits against the distributed cap and replies ``admitted`` or
``rejected``. Subsequent binary frames are PCM (dropped for now); text frames act as a
heartbeat that renews the lease.

TODO(step 2): replace the placeholder token with real authn/authz at the edge.
TODO(step 3+): on admit, upsert meeting/session rows, start Soniox + the chunker, and feed
decoded PCM into the ClientCaptureAdapter -> Session Manager.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.logging import get_logger
from app.session.adapter import ClientCaptureAdapter

router = APIRouter(tags=["ingest"])
log = get_logger("ingest")


@router.websocket("/ingest")
async def ingest_ws(ws: WebSocket, token: str | None = Query(default=None)) -> None:
    await ws.accept()
    registry = ws.app.state.registry
    call_id: str | None = None
    frames = 0
    try:
        hello = await ws.receive_json()
        if hello.get("type") != "hello":
            await ws.send_json({"type": "error", "reason": "expected_hello"})
            return

        platform = hello.get("platform")
        external_meeting_id = hello.get("external_meeting_id")
        call_id = hello.get("call_id")
        if not (platform and external_meeting_id and call_id):
            await ws.send_json({"type": "error", "reason": "missing_fields"})
            call_id = None
            return

        if not await registry.acquire(call_id):
            active = await registry.active_count()
            await ws.send_json({"type": "rejected", "reason": "at_capacity", "active": active})
            call_id = None  # never acquired -> nothing to release
            return

        ClientCaptureAdapter(platform, external_meeting_id, call_id)
        await ws.send_json(
            {"type": "admitted", "call_id": call_id, "worker_id": registry.worker_id}
        )
        log.info("ingest.admitted", call_id=call_id, platform=platform)

        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                frames += 1  # TODO(step 3): decode + feed Session Manager
            elif msg.get("text") is not None:
                if not await registry.renew(call_id):
                    log.warning("ingest.lease_lost", call_id=call_id)
                    break
    except WebSocketDisconnect:
        pass
    finally:
        if call_id:
            await registry.release(call_id)
            log.info("ingest.closed", call_id=call_id, frames=frames)
