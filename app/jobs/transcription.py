"""Upload-transcription worker (spec v3 §17).

Claims queued upload jobs (FOR UPDATE SKIP LOCKED, capped concurrency), downloads each file, runs
the owner's Soniox key through the async file API, persists the result as a single-session meeting
transcript, and translates per the meeting's config. Runs out-of-band (the scheduler service / a
manual ``python -m app.jobs.transcription`` run) so the API process stays responsive.

Crash recovery: a job stuck in ``processing`` after a worker death is a documented follow-up
(stale-claim reaper); v1 leaves it for an operator to re-queue.
"""

from __future__ import annotations

import asyncio
import uuid

from app.config import DEV_ENVS, Settings, get_settings, soniox_file_base
from app.crypto import decrypt
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.db.models import JobStatus, SessionStatus, TranscriptionJob, TranslationStatus
from app.logging import configure_logging, get_logger
from app.storage.factory import make_storage
from app.transcribe.file_factory import make_file_transcriber
from app.translate.resolve import build_user_translator

log = get_logger("transcription")


def _decrypt_key(enc: str | None, *, settings: Settings, user_id: uuid.UUID) -> str | None:
    if not enc:
        return None
    try:
        return decrypt(enc, secret=settings.secret_key, aad=str(user_id))
    except Exception as exc:  # noqa: BLE001 — corrupt/rotated key behaves as "no key"
        log.warning("transcription.key_decrypt_failed", user_id=str(user_id), error=repr(exc))
        return None


async def _fail(factory, job_id: uuid.UUID, reason: str) -> None:
    # Guarded: only a still-processing job fails (a concurrent cancel must not be overwritten).
    async with factory() as db:
        await repo.finish_job(db, job_id=job_id, status=JobStatus.failed, error=reason)
        await db.commit()
    log.warning("transcription.failed", job_id=str(job_id), reason=reason)


async def process_job(
    job: TranscriptionJob, *, factory, storage, settings: Settings, run_id: str
) -> None:
    """Process one already-claimed (status=processing) job to done/failed.

    Persistence mirrors the live pipeline: each segment commits in its own short transaction (no
    long-held meeting lock, partial-safe) and translation runs decoupled (no DB lock across the LLM
    calls). The terminal done-transition is guarded so a concurrent cancel always wins, and any
    unexpected error routes to _fail so a job never wedges in 'processing'.
    """
    try:
        async with factory() as db:
            meeting = await repo.get_meeting(db, job.meeting_id)
            owner = await repo.get_user_by_id(db, job.owner_id)
        if meeting is None or owner is None:
            await _fail(factory, job.id, "meeting or owner no longer exists")
            return

        soniox_key = _decrypt_key(owner.soniox_key_enc, settings=settings, user_id=owner.id)
        if soniox_key is None and settings.app_env not in DEV_ENVS:
            await _fail(factory, job.id, "no Soniox API key configured for the owner")
            return

        # The owner's region travels with their key (data residency); fall back to the server
        # region only when using the shared server key (no per-user key, dev/test).
        region = owner.soniox_region if soniox_key else settings.soniox_region
        transcriber = make_file_transcriber(
            api_key=soniox_key,
            language_hints=settings.language_hints,
            base_url=soniox_file_base(region),
        )
        try:
            audio = await storage.download(job.s3_key)
            segments = await transcriber.transcribe(
                audio, language_hints=settings.language_hints
            )
        except Exception as exc:  # noqa: BLE001 — provider/IO failure -> job fails, not the worker
            await _fail(factory, job.id, f"transcription failed: {exc!r}")
            return

        # Honor a cancel that landed while we were transcribing; create the session only if live.
        async with factory() as db:
            fresh = await repo.get_transcription_job(db, job.id)
            if fresh is None or fresh.status == JobStatus.canceled:
                log.info("transcription.canceled_midflight", job_id=str(job.id))
                return
            session = await repo.create_session(
                db, meeting_id=job.meeting_id, platform_call_id=f"upload:{job.id}", run_id=run_id
            )
            session_id = session.id
            await db.commit()

        # Persist each segment in its own short transaction (no long lock; partial-safe).
        persisted: list[tuple[uuid.UUID, str, str | None]] = []
        for i, seg in enumerate(segments):
            async with factory() as db:
                seg_id = await repo.upsert_segment(
                    db,
                    meeting_id=job.meeting_id,
                    session_id=session_id,
                    speaker_id=seg.speaker_id,
                    utterance_id=f"u{i + 1}",
                    text=seg.text,
                    language=seg.language,
                    start_ms=seg.start_ms,
                    end_ms=seg.end_ms,
                )
                await db.commit()
            persisted.append((seg_id, seg.text, seg.language))

        # Translate decoupled: the LLM calls hold no DB lock; each result commits on its own.
        if meeting.translation_enabled and meeting.translation_output_language:
            target = meeting.translation_output_language
            translator = build_user_translator(
                owner, settings=settings, model=meeting.translation_model
            )
            if translator is not None:
                for seg_id, text, language in persisted:
                    if target == language or not text.strip():
                        continue
                    try:
                        out = await translator.translate(
                            text, source_language=language, target_languages=[target]
                        )
                    except Exception as exc:  # noqa: BLE001 — translation is best-effort
                        log.warning(
                            "transcription.translate_failed", job_id=str(job.id), error=repr(exc)
                        )
                        out = {}
                    ok = target in out
                    async with factory() as db:
                        await repo.upsert_translation(
                            db,
                            segment_id=seg_id,
                            target_language=target,
                            text=out.get(target, ""),
                            status=TranslationStatus.ok if ok else TranslationStatus.failed,
                        )
                        await db.commit()

        # Finalize: end the session and transition done — guarded, so a mid-flight cancel wins.
        async with factory() as db:
            await repo.mark_session(
                db,
                session_id=session_id,
                status=SessionStatus.ended,
                ended_reason="upload_complete",
            )
            done = await repo.finish_job(db, job_id=job.id, status=JobStatus.done)
            await db.commit()
        if done:
            log.info("transcription.done", job_id=str(job.id), segments=len(segments))
        else:
            log.info("transcription.canceled_before_done", job_id=str(job.id))
    except Exception as exc:  # noqa: BLE001 — never leave a job wedged in 'processing'
        log.warning("transcription.unexpected_error", job_id=str(job.id), error=repr(exc))
        await _fail(factory, job.id, f"unexpected error: {exc!r}")


async def run() -> int:
    """One pass: claim up to the concurrency cap of queued jobs and process them. Returns count."""
    settings = get_settings()
    configure_logging()
    run_id = uuid.uuid4().hex
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    storage = make_storage(settings)
    try:
        async with factory() as db:
            jobs = await repo.claim_queued_jobs(
                db, run_id=run_id, limit=settings.upload_max_concurrent
            )
            await db.commit()
        if jobs:
            # return_exceptions: one job's failure must not cancel its in-flight siblings
            # (process_job already routes its own errors to _fail; this is belt-and-suspenders).
            results = await asyncio.gather(
                *(
                    process_job(
                        j, factory=factory, storage=storage, settings=settings, run_id=run_id
                    )
                    for j in jobs
                ),
                return_exceptions=True,
            )
            for j, res in zip(jobs, results, strict=False):
                if isinstance(res, Exception):
                    log.warning(
                        "transcription.job_crashed", job_id=str(j.id), error=repr(res)
                    )
        return len(jobs)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
