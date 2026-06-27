"""MeetingSession abstraction + the v1 ClientCaptureAdapter (spec v3 §4-5).

The adapter turns the authenticated wss:// frames from the local capture client into the
normalized event stream. Audio framing on the wire: a JSON ``hello`` first, then binary PCM
frames (16 kHz mono) carrying ``(seq, timestamp, gap_flag)`` headers; control frames as JSON.

This is the seam: the Session Manager (a later build step) consumes ``events()`` and is blind
to whether capture came from a local Mac client or a future cloud host.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.session.events import Event


@runtime_checkable
class MeetingSession(Protocol):
    """A normalized capture session yielding lifecycle + audio events."""

    @property
    def call_id(self) -> str: ...

    def events(self) -> AsyncIterator[Event]: ...


class ClientCaptureAdapter:
    """Adapts the local capture client's wss:// stream to MeetingSession events.

    Skeleton: holds the parsed identity and exposes an async event queue that the ingest
    endpoint feeds. The full frame decoder + backpressure are wired in build-order step 3.
    """

    def __init__(self, platform: str, external_meeting_id: str, call_id: str) -> None:
        self._platform = platform
        self._external_meeting_id = external_meeting_id
        self._call_id = call_id

    @property
    def call_id(self) -> str:
        return self._call_id

    @property
    def platform(self) -> str:
        return self._platform

    @property
    def external_meeting_id(self) -> str:
        return self._external_meeting_id

    async def events(self) -> AsyncIterator[Event]:  # pragma: no cover - filled in step 3
        raise NotImplementedError("event decoding is wired in build-order step 3")
        yield  # type: ignore[unreachable]
