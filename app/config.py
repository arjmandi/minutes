"""Application configuration (pydantic-settings).

All settings load from environment variables prefixed ``MINUTES_`` or a local ``.env``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MINUTES_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"

    # Datastores
    database_url: str = "postgresql+asyncpg://minutes:minutes@localhost:5432/minutes"
    redis_url: str = "redis://localhost:6379/0"

    # Object storage (S3 protocol: MinIO locally, S3-compatible object storage in prod)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "eu-central-1"
    s3_bucket: str = "minutes-audio"
    s3_access_key: str = "minutes"
    s3_secret_key: str = "minutes-secret"

    # External sub-processors
    soniox_api_key: str = ""
    anthropic_api_key: str = ""

    # Admission control
    max_concurrent_calls: int = 5

    # Capture / archival
    chunk_interval_minutes: int = 15
    language_hints: list[str] = Field(default_factory=lambda: ["en", "de", "fa"])

    # Timing budget (seconds). Invariants (spec v3 §13):
    #   lease_ttl_s > max event-loop/Redis stall
    #   drain_deadline_s + upload_p99 < grace_period (set at deploy)
    lease_ttl_s: int = 60
    heartbeat_s: int = 20
    join_timeout_s: int = 45
    idle_timeout_s: int = 120
    media_inactivity_s: int = 30
    drain_deadline_s: int = 25

    # GDPR
    retention_days: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()
