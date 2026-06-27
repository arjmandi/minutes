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
from app.audio.wav import BYTES_PER_SECOND, wrap_pcm
from app.db import repo
from app.db.models import ChunkState, SessionStatus
from app.logging import get_logger
from app.session.adapter import ClientCaptureAdapter
from app.session.events import AudioFrame, EndReason, SessionEnded, SessionStarted
from app.storage.base import Storage
from app.transcribe.base import FinalSegment, Transcriber
from app.transcribe.factory import TranscriberFactory
from app.translate.base import Translator
from app.translate.factory import TranslatorFactory

log = get_logger("session")
MIXED = "mixed"
_AUDIO_QUEUE_MAX = 256
_TRANSLATION_QUEUE_MAX = 256
_FANOUT_QUEUE_MAX = 512  # interims are frequent; cap separately, drop-on-full off the STT path
_CHUNK_QUEUE_MAX = 2000  # ~200s buffer so the archive rarely drops during a rotation upload
_PUT_TIMEOUT_S = 1.0
_PUBLISH_TIMEOUT_S = 2.0
_CHUNK_UPLOAD_TIMEOUT_S = 120.0  # bound a hung upload (a real one is seconds); leaves PENDING


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
        storage: Storage,
        chunk_interval_s: int,
        worker_id: str,
        finalize_timeout_s: int = 30,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis
        self._transcriber_factory = transcriber_factory
        self._translator_factory = translator_factory
        self._translation_targets = translation_targets
        self._storage = storage
        self._chunk_interval_s = chunk_interval_s
        self._worker_id = worker_id
        self._finalize_timeout_s = finalize_timeout_s

    async def _flush_chunk(self, sid: uuid.UUID, seq: int, pcm: bytes) -> None:
        """Two-phase archive write: reserve row -> upload WAV -> mark recorded (spec v3 §9).
        On failure the PENDING row is left for app.jobs.reconcile to resolve (RECORDED / LOST)."""
        key = f"sessions/{sid}/{MIXED}/{seq:06d}.wav"
        try:
            async with self._session_factory() as db:
                chunk_id = await repo.reserve_chunk(
                    db,
                    session_id=sid,
                    speaker_id=MIXED,
                    s3_key=key,
                    seq=seq,
                    duration_s=len(pcm) / BYTES_PER_SECOND,
                )
                await db.commit()
            await asyncio.wait_for(
                self._storage.upload(key, wrap_pcm(pcm)), timeout=_CHUNK_UPLOAD_TIMEOUT_S
            )
            async with self._session_factory() as db:
                await repo.mark_chunk(db, chunk_id=chunk_id, state=ChunkState.recorded)
                await db.commit()
        except Exception as exc:  # noqa: BLE001 — leave PENDING for the reconciler
            log.warning(
                "session.chunk_upload_failed", session_id=str(sid), seq=seq, error=repr(exc)
            )

    async def run(self, adapter: ClientCaptureAdapter) -> CallSummary:
        meeting_id: uuid.UUID | None = None
        session_id: uuid.UUID | None = None
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_AUDIO_QUEUE_MAX)
        translation_q: asyncio.Queue = asyncio.Queue(maxsize=_TRANSLATION_QUEUE_MAX)
        fanout_q: asyncio.Queue = asyncio.Queue(maxsize=_FANOUT_QUEUE_MAX)
        chunk_q: asyncio.Queue = asyncio.Queue(maxsize=_CHUNK_QUEUE_MAX)
        persist_task: asyncio.Task[int] | None = None
        translate_task: asyncio.Task | None = None
        fanout_task: asyncio.Task | None = None
        chunker_task: asyncio.Task | None = None
        segments = 0
        reason = EndReason.normal

        async def audio_gen() -> AsyncIterator[bytes]:
            while True:
                item = await audio_q.get()
                if item is None:
                    return
                yield item

        def enqueue_fanout(event: dict) -> None:
            # Off the STT hot path: drop on a full queue rather than ever block transcription.
            try:
                fanout_q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("session.fanout_dropped", session_id=str(session_id))

        async def fanout_worker(mid: uuid.UUID) -> None:
            while True:
                item = await fanout_q.get()
                if item is None:
                    return
                try:
                    await asyncio.wait_for(
                        fanout.publish(self._redis, mid, item), timeout=_PUBLISH_TIMEOUT_S
                    )
                except Exception:  # noqa: BLE001 — best-effort (incl. a hung/slow Redis)
                    pass

        async def chunker_worker(sid: uuid.UUID) -> None:
            rotate_bytes = max(self._chunk_interval_s, 1) * BYTES_PER_SECOND
            async with self._session_factory() as db:
                seq = await repo.next_chunk_seq(db, session_id=sid, speaker_id=MIXED)  # no reset
            buf = bytearray()
            while True:
                item = await chunk_q.get()
                if item is None:
                    if buf:
                        await self._flush_chunk(sid, seq, bytes(buf))  # flush final partial
                    return
                buf.extend(item)
                if len(buf) >= rotate_bytes:
                    await self._flush_chunk(sid, seq, bytes(buf))
                    seq += 1
                    buf = bytearray()

        async def persist(transcriber: Transcriber, mid: uuid.UUID, sid: uuid.UUID) -> int:
            count = 0
            gen = transcriber.stream(audio_gen())
            try:
                async for event in gen:
                    if not isinstance(event, FinalSegment):
                        enqueue_fanout({"kind": "interim", "text": event.text})
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
                    # Durable + decoupled work first, then live fan-out; all non-blocking.
                    if self._translation_targets:
                        try:
                            translation_q.put_nowait((seg_id, event.text, event.language))
                        except asyncio.QueueFull:
                            log.warning("session.translation_dropped", session_id=str(sid))
                    enqueue_fanout(
                        {
                            "kind": "final",
                            "id": str(seg_id),
                            "text": event.text,
                            "language": event.language,
                            "speaker": MIXED,
                            "start_ms": event.start_ms,
                            "end_ms": event.end_ms,
                        }
                    )
            finally:
                await gen.aclose()
            return count

        async def translate_worker(translator: Translator, sid: uuid.UUID) -> None:
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
                        enqueue_fanout(
                            {
                                "kind": "translation",
                                "segment_id": str(seg_id),
                                "target": target,
                                "text": translated,
                            }
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
                        translate_worker(self._translator_factory(None), session_id)
                    )
                    fanout_task = asyncio.create_task(fanout_worker(meeting_id))
                    chunker_task = asyncio.create_task(chunker_worker(session_id))
                    log.info("session.started", session_id=str(session_id), call_id=event.call_id)
                elif isinstance(event, AudioFrame):
                    if not await put_audio(event.pcm):
                        break  # consumer died -> stop; finally will finalize
                    try:
                        chunk_q.put_nowait(event.pcm)  # tee to the archive (drop-on-full)
                    except asyncio.QueueFull:
                        log.warning("session.chunk_dropped", session_id=str(session_id))
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
            # Drain live fan-out (bounded).
            if fanout_task is not None:
                _put_sentinel(fanout_q)
                try:
                    await asyncio.wait_for(fanout_task, timeout=self._finalize_timeout_s)
                except TimeoutError:
                    fanout_task.cancel()
                    await asyncio.gather(fanout_task, return_exceptions=True)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "session.fanout_drain_failed",
                        session_id=str(session_id),
                        error=repr(exc),
                    )
            # Drain the chunker: flush the final partial chunk to storage (bounded).
            if chunker_task is not None:
                _put_sentinel(chunk_q)
                try:
                    await asyncio.wait_for(chunker_task, timeout=self._finalize_timeout_s)
                except TimeoutError:
                    log.error("session.chunk_flush_timeout", session_id=str(session_id))
                    chunker_task.cancel()
                    await asyncio.gather(chunker_task, return_exceptions=True)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "session.chunk_drain_failed",
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
