"""Audio upload -> async file transcription (spec v3 §17), owner-scoped.

POST /uploads accepts an audio file, archives it to object storage, creates a single-session
``upload`` meeting owned by the caller (translation config seeded from their defaults), and queues a
transcription job. The job is processed out-of-band by app.jobs.transcription (the scheduler). The
caller polls GET /uploads/{id}; DELETE /uploads/{id} cancels.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.auth.dependencies import require_user
from app.db import repo
from app.db.models import JobStatus, TranscriptionJob, User
from app.logging import get_logger

router = APIRouter(prefix="/uploads", tags=["uploads"])
log = get_logger("uploads")


def _job_dict(j: TranscriptionJob) -> dict:
    return {
        "id": str(j.id),
        "meeting_id": str(j.meeting_id),
        "status": j.status.value,
        "original_filename": j.original_filename,
        "content_type": j.content_type,
        "size_bytes": j.size_bytes,
        "error": j.error,
        "created_at": j.created_at.isoformat(),
        "updated_at": j.updated_at.isoformat(),
    }


@router.post("")
async def create_upload(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
) -> dict:
    settings = request.app.state.settings
    content_type = file.content_type
    if content_type and not (
        content_type.startswith("audio/") or content_type.startswith("video/")
    ):
        raise HTTPException(status_code=415, detail="expected an audio/* file")

    # Reject oversize before buffering: trust Content-Length if present, then hard-cap the read.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.upload_max_bytes:
        raise HTTPException(status_code=413, detail="file too large")
    data = await file.read(settings.upload_max_bytes + 1)
    if len(data) > settings.upload_max_bytes:
        raise HTTPException(status_code=413, detail="file too large")
    if not data:
        raise HTTPException(status_code=422, detail="empty file")

    external_id = f"upload:{uuid.uuid4().hex}"
    s3_key = f"uploads/{external_id}/source"
    await request.app.state.storage.upload(
        s3_key, data, content_type=content_type or "application/octet-stream"
    )
    async with request.app.state.session_factory() as db:
        meeting = await repo.upsert_meeting(
            db, platform="upload", external_meeting_id=external_id, owner_id=user.id
        )
        await repo.set_meeting_title(db, meeting_id=meeting.id, title=file.filename)
        await repo.seed_meeting_translation(
            db,
            meeting_id=meeting.id,
            enabled=user.default_translation_on,
            output_language=user.default_output_language,
            model=user.default_model,
        )
        job = await repo.create_transcription_job(
            db,
            owner_id=user.id,
            meeting_id=meeting.id,
            s3_key=s3_key,
            original_filename=file.filename,
            content_type=content_type,
            size_bytes=len(data),
        )
        await db.commit()
        log.info("upload.queued", job_id=str(job.id), meeting_id=str(meeting.id), bytes=len(data))
        return _job_dict(job)


@router.get("")
async def list_uploads(request: Request, user: User = Depends(require_user)) -> list[dict]:
    async with request.app.state.session_factory() as db:
        jobs = await repo.list_transcription_jobs_for_user(
            db, user_id=user.id, is_admin=user.is_admin
        )
    return [_job_dict(j) for j in jobs]


def _authorized(job: TranscriptionJob, user: User) -> bool:
    return user.is_admin or job.owner_id == user.id


@router.get("/{job_id}")
async def get_upload(
    job_id: uuid.UUID, request: Request, user: User = Depends(require_user)
) -> dict:
    async with request.app.state.session_factory() as db:
        job = await repo.get_transcription_job(db, job_id)
        if job is None or not _authorized(job, user):
            raise HTTPException(status_code=404, detail="not found")
        return _job_dict(job)


@router.delete("/{job_id}")
async def cancel_upload(
    job_id: uuid.UUID, request: Request, user: User = Depends(require_user)
) -> dict:
    async with request.app.state.session_factory() as db:
        job = await repo.get_transcription_job(db, job_id)
        if job is None or not _authorized(job, user):
            raise HTTPException(status_code=404, detail="not found")
        canceled = await repo.cancel_job(db, job_id=job_id)
        await db.commit()
        if not canceled:
            raise HTTPException(status_code=409, detail=f"job already {job.status.value}")
        return {"id": str(job_id), "status": JobStatus.canceled.value}
