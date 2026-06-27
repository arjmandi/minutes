"""Per-call Session Manager (spec v3 §13).

Drives a MeetingSession adapter through transcription + persistence: upserts the meeting/session
rows (with the worker's run_id fence), streams audio into the Transcriber over a bounded queue,
and persists each final segment. Robustness contract: if the transcriber/DB/Redis fails mid-call,
the producer detects the dead consumer (rather than blocking forever on the bounded queue),
teardown is non-blocking, and ``run`` always returns so the ingest endpoint can release the slot.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.db import repo
from app.db.models import SessionStatus
from app.logging import get_logger
from app.session.adapter import ClientCaptureAdapter
from app.session.events import AudioFrame, EndReason, SessionEnded, SessionStarted
from app.transcribe.base import FinalSegment, Transcriber
from app.transcribe.factory import TranscriberFactory

log = get_logger("session")
MIXED = "mixed"
_AUDIO_QUEUE_MAX = 256
_PUT_TIMEOUT_S = 1.0


@dataclass(slots=True)
class CallSummary:
    session_id: str | None
    segments: int
    reason: str


class SessionManager:
    def __init__(
        self,
        *,
        session_factory,
        redis,
        transcriber_factory: TranscriberFactory,
        worker_id: str,
        finalize_timeout_s: int = 30,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis
        self._transcriber_factory = transcriber_factory
        self._worker_id = worker_id
        self._finalize_timeout_s = finalize_timeout_s

    async def run(self, adapter: ClientCaptureAdapter) -> CallSummary:
        meeting_id: uuid.UUID | None = None
        session_id: uuid.UUID | None = None
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_AUDIO_QUEUE_MAX)
        persist_task: asyncio.Task[int] | None = None
        segments = 0
        reason = EndReason.normal

        async def audio_gen() -> AsyncIterator[bytes]:
            while True:
                item = await audio_q.get()
                if item is None:
                    return
                yield item

        async def persist(transcriber: Transcriber, mid: uuid.UUID, sid: uuid.UUID) -> int:
            count = 0
            gen = transcriber.stream(audio_gen())
            try:
                async for event in gen:
                    if isinstance(event, FinalSegment):
                        try:
                            async with self._session_factory() as db:
                                await repo.upsert_segment(
                                    db,
                                    meeting_id=mid,
                                    session_id=sid,
                                    speaker_id=MIXED,
                                    utterance_id=event.utterance_id,
                                    text=event.text,
                                    language=event.language,
                                    start_ms=event.start_ms,
                                    end_ms=event.end_ms,
                                )
                                await db.commit()
                            count += 1
                        except Exception as exc:  # noqa: BLE001 — best-effort write
                            log.warning(
                                "session.segment_persist_failed",
                                session_id=str(sid),
                                error=repr(exc),
                            )
                    # Interim events feed live fan-out in a later build step (not persisted).
            finally:
                await gen.aclose()  # ensure the transcriber tears down its socket/tasks
            return count

        async def put_audio(pcm: bytes) -> bool:
            """Enqueue audio; return False if the consumer (persist_task) has died."""
            while True:
                if persist_task is not None and persist_task.done():
                    return False
                try:
                    await asyncio.wait_for(audio_q.put(pcm), timeout=_PUT_TIMEOUT_S)
                    return True
                except TimeoutError:
                    continue  # consumer alive but slow -> backpressure, retry

        try:
            async for event in adapter.events():
                if isinstance(event, SessionStarted):
                    async with self._session_factory() as db:
                        meeting = await repo.upsert_meeting(
                            db,
                            platform=event.platform,
                            external_meeting_id=event.external_meeting_id,
                        )
                        meeting_id = meeting.id
                        await db.commit()
                    async with self._session_factory() as db:
                        session = await repo.create_session(
                            db,
                            meeting_id=meeting_id,
                            platform_call_id=event.call_id,
                            run_id=self._worker_id,
                        )
                        session_id = session.id
                        await db.commit()
                    transcriber = self._transcriber_factory(None)
                    persist_task = asyncio.create_task(persist(transcriber, meeting_id, session_id))
                    log.info("session.started", session_id=str(session_id), call_id=event.call_id)
                elif isinstance(event, AudioFrame):
                    if not await put_audio(event.pcm):
                        break  # consumer died -> stop; finally will finalize
                elif isinstance(event, SessionEnded):
                    reason = event.reason
                    break
        finally:
            if persist_task is not None and not persist_task.done():
                _put_sentinel(audio_q)  # non-blocking end-of-audio
            if persist_task is not None:
                try:
                    segments = await asyncio.wait_for(
                        persist_task, timeout=self._finalize_timeout_s
                    )
                except TimeoutError:
                    log.error("session.finalize_timeout", session_id=str(session_id))
                    persist_task.cancel()
                    await asyncio.gather(persist_task, return_exceptions=True)
                    reason = EndReason.error
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "session.transcribe_failed", session_id=str(session_id), error=repr(exc)
                    )
                    reason = EndReason.error
            if session_id is not None:
                status = SessionStatus.ended if reason == EndReason.normal else SessionStatus.failed
                async with self._session_factory() as db:
                    await repo.mark_session(
                        db, session_id=session_id, status=status, ended_reason=str(reason)
                    )
                    await db.commit()
                log.info(
                    "session.ended",
                    session_id=str(session_id),
                    segments=segments,
                    reason=str(reason),
                )

        return CallSummary(
            session_id=str(session_id) if session_id else None,
            segments=segments,
            reason=str(reason),
        )


def _put_sentinel(audio_q: asyncio.Queue[bytes | None]) -> None:
    """Place the end-of-audio sentinel without blocking, evicting one buffered frame if full."""
    try:
        audio_q.put_nowait(None)
    except asyncio.QueueFull:
        try:
            audio_q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            audio_q.put_nowait(None)
        except asyncio.QueueFull:
            pass
