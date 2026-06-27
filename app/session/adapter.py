"""MeetingSession abstraction + the v1 ClientCaptureAdapter (spec v3 §4-5).

The ingest endpoint decodes wss frames and calls ``feed()``/``end()``; the Session Manager
consumes the normalized event stream via ``events()``. ``SessionStarted`` is emitted first
(before any audio), so the backend is blind to whether capture came from a local Mac client or a
future cloud host. The internal queue is bounded → a slow consumer back-pressures ingest.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.audio.frames import PcmFrame
from app.session.events import AudioFrame, EndReason, Event, SessionEnded, SessionStarted


@runtime_checkable
class MeetingSession(Protocol):
    @property
    def call_id(self) -> str: ...

    def events(self) -> AsyncIterator[Event]: ...


@dataclass(slots=True)
class _End:
    reason: EndReason


class ClientCaptureAdapter:
    def __init__(
        self,
        platform: str,
        external_meeting_id: str,
        call_id: str,
        *,
        maxsize: int = 256,
    ) -> None:
        self._platform = platform
        self._external_meeting_id = external_meeting_id
        self._call_id = call_id
        self._queue: asyncio.Queue[PcmFrame | _End] = asyncio.Queue(maxsize=maxsize)

    @property
    def call_id(self) -> str:
        return self._call_id

    async def feed(self, frame: PcmFrame) -> None:
        await self._queue.put(frame)

    async def end(self, reason: EndReason = EndReason.normal) -> None:
        # Non-blocking: end-of-stream supersedes any buffered audio, so evict on a full queue
        # rather than block teardown (a wedged consumer must not hang the cleanup path).
        item = _End(reason)
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                pass

    async def events(self) -> AsyncIterator[Event]:
        yield SessionStarted(self._platform, self._external_meeting_id, self._call_id)
        while True:
            item = await self._queue.get()
            if isinstance(item, _End):
                yield SessionEnded(item.reason)
                return
            yield AudioFrame(pcm=item.pcm, timestamp=item.ts_ms / 1000.0)
