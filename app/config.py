"""Application configuration (pydantic-settings).

All settings load from environment variables prefixed ``MINUTES_`` or a local ``.env``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel dev secret; the startup guard refuses it (and any <32-byte secret) outside dev.
DEFAULT_AUTH_SECRET = "dev-insecure-change-me-please-override-in-real-envs"
DEV_ENVS = frozenset({"local", "dev", "test"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MINUTES_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "dev", "test", "staging", "prod"] = "local"

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

    # Auth edge — capability tokens (spec v3 §15). The startup guard requires a strong,
    # non-default secret whenever app_env is not a dev env.
    auth_secret: str = DEFAULT_AUTH_SECRET
    auth_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    auth_token_ttl_s: int = 3600

    # Admission control
    max_concurrent_calls: int = 5

    # Capture / archival
    chunk_interval_minutes: int = 15
    language_hints: list[str] = Field(default_factory=lambda: ["en", "de", "fa"])

    # Translation (LLM, downstream of finalized segments)
    translation_model: str = "claude-haiku-4-5-20251001"
    translation_targets: list[str] = Field(default_factory=lambda: ["en"])

    # Timing budget (seconds). Invariants (spec v3 §13):
    #   lease_ttl_s > max event-loop/Redis stall
    #   drain_deadline_s + upload_p99 < grace_period (set at deploy)
    lease_ttl_s: int = 60
    heartbeat_s: int = 20
    join_timeout_s: int = 45
    idle_timeout_s: int = 120
    media_inactivity_s: int = 30
    drain_deadline_s: int = 25
    finalize_timeout_s: int = 30  # bound on awaiting the per-call pipeline during teardown

    # GDPR
    retention_days: int = 90

    @model_validator(mode="after")
    def _enforce_strong_secret_outside_dev(self) -> Settings:
        """Fail closed: never run a non-dev env on the public default / a weak secret."""
        if self.app_env not in DEV_ENVS and (
            self.auth_secret == DEFAULT_AUTH_SECRET or len(self.auth_secret) < 32
        ):
            raise ValueError(
                "MINUTES_AUTH_SECRET must be a strong (>=32 byte) non-default secret when "
                f"MINUTES_APP_ENV is '{self.app_env}'. Generate one with: openssl rand -hex 32"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
