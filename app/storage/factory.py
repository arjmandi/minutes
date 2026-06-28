"""Build the storage backend from settings: real S3/MinIO if enabled, else the in-memory fake."""

from __future__ import annotations

from app.config import Settings
from app.logging import get_logger
from app.storage.base import Storage
from app.storage.fake import FakeStorage
from app.storage.spaces import SpacesStorage

log = get_logger("storage")


def make_storage(settings: Settings) -> Storage:
    if settings.s3_enabled:
        log.info("storage.backend", backend="spaces", bucket=settings.s3_bucket)
        return SpacesStorage(
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        )
    log.info("storage.backend", backend="fake")
    return FakeStorage()
