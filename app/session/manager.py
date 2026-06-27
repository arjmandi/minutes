"""Per-call Session Manager (spec v3 §13).

Drives a MeetingSession adapter through transcription, persistence, and downstream translation.
Upserts meeting/session rows (run_id fence), streams audio into the Transcriber over a bounded
queue, persists each final segment, and hands finals to a separate bounded translation worker
(LLM, downstream — translation lag/failure never blocks STT). Robustness contract: a dead
consumer is detected (no blocking on bounded queues), teardown is non-blocking, and ``run`` always
returns so the ingest endpoint can release the admission slot.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app import fanout
from app.db import repo
from app.db.models import SessionStatus
from app.logging import get_logger
from app.session.adapter import ClientCaptureAdapter
from app.session.events import AudioFrame, EndReason, SessionEnded, SessionStarted
from app.transcribe.base import FinalSegment, Transcriber
from app.transcribe.factory import TranscriberFactory
from app.translate.base import Translator
from app.translate.factory import TranslatorFactory

log = get_logger("session")
MIXED = "mixed"
_AUDIO_QUEUE_MAX = 256
_TRANSLATION_QUEUE_MAX = 256
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
        translator_factory: TranslatorFactory,
        translation_targets: list[str],
        worker_id: str,
        finalize_timeout_s: int = 30,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis
        self._transcriber_factory = transcriber_factory
        self._translator_factory = translator_factory
        self._translation_targets = translation_targets
        self._worker_id = worker_id
        self._finalize_timeout_s = finalize_timeout_s

    async def run(self, adapter: ClientCaptureAdapter) -> CallSummary:
        meeting_id: uuid.UUID | None = None
        session_id: uuid.UUID | None = None
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_AUDIO_QUEUE_MAX)
        translation_q: asyncio.Queue = asyncio.Queue(maxsize=_TRANSLATION_QUEUE_MAX)
        persist_task: asyncio.Task[int] | None = None
        translate_task: asyncio.Task | None = None
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
                    if not isinstance(event, FinalSegment):
                        await fanout.publish(
                            self._redis, mid, {"kind": "interim", "text": event.text}
                        )
                        continue  # interim is live-only, not persisted
                    try:
                        async with self._session_factory() as db:
                            seg_id = await repo.upsert_segment(
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
                    except Exception as exc:  # noqa: BLE001 — a transient write must not kill the call
                        log.warning(
                            "session.segment_persist_failed", session_id=str(sid), error=repr(exc)
                        )
                        continue
                    await fanout.publish(
                        self._redis,
                        mid,
                        {
                            "kind": "final",
                            "id": str(seg_id),
                            "text": event.text,
                            "language": event.language,
                            "speaker": MIXED,
                            "start_ms": event.start_ms,
                            "end_ms": event.end_ms,
                        },
                    )
                    # Hand off to translation: best-effort, non-blocking (never back-pressure STT).
                    if self._translation_targets:
                        try:
                            translation_q.put_nowait((seg_id, event.text, event.language))
                        except asyncio.QueueFull:
                            log.warning("session.translation_dropped", session_id=str(sid))
            finally:
                await gen.aclose()
            return count

        async def translate_worker(translator: Translator, mid: uuid.UUID, sid: uuid.UUID) -> None:
            while True:
                item = await translation_q.get()
                if item is None:
                    return
                seg_id, text, source_lang = item
                try:
                    out = await translator.translate(
                        text,
                        source_language=source_lang,
                        target_languages=self._translation_targets,
                    )
                    if not out:
                        continue
                    async with self._session_factory() as db:
                        for target, translated in out.items():
                            await repo.upsert_translation(
                                db, segment_id=seg_id, target_language=target, text=translated
                            )
                        await db.commit()
                    for target, translated in out.items():
                        await fanout.publish(
                            self._redis,
                            mid,
                            {
                                "kind": "translation",
                                "segment_id": str(seg_id),
                                "target": target,
                                "text": translated,
                            },
                        )
                except Exception as exc:  # noqa: BLE001 — translation is best-effort
                    log.warning("session.translate_failed", session_id=str(sid), error=repr(exc))

        async def put_audio(pcm: bytes) -> bool:
            while True:
                if persist_task is not None and persist_task.done():
                    return False
                try:
                    await asyncio.wait_for(audio_q.put(pcm), timeout=_PUT_TIMEOUT_S)
                    return True
                except TimeoutError:
                    continue

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
                    persist_task = asyncio.create_task(
                        persist(self._transcriber_factory(None), meeting_id, session_id)
                    )
                    translate_task = asyncio.create_task(
                        translate_worker(self._translator_factory(None), meeting_id, session_id)
                    )
                    log.info("session.started", session_id=str(session_id), call_id=event.call_id)
                elif isinstance(event, AudioFrame):
                    if not await put_audio(event.pcm):
                        break  # consumer died -> stop; finally will finalize
                elif isinstance(event, SessionEnded):
                    reason = event.reason
                    break
        finally:
            if persist_task is not None and not persist_task.done():
                _put_sentinel(audio_q)
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
            # Drain translations after transcription completes (bounded).
            if translate_task is not None:
                _put_sentinel(translation_q)
                try:
                    await asyncio.wait_for(translate_task, timeout=self._finalize_timeout_s)
                except TimeoutError:
                    log.error("session.translate_timeout", session_id=str(session_id))
                    translate_task.cancel()
                    await asyncio.gather(translate_task, return_exceptions=True)
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "session.translate_drain_failed",
                        session_id=str(session_id),
                        error=repr(exc),
                    )
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


def _put_sentinel(queue: asyncio.Queue) -> None:
    """Place the end-of-stream sentinel (None) without blocking, evicting one item if full."""
    try:
        queue.put_nowait(None)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
