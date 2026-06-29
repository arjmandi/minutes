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
DEFAULT_SECRET_KEY = "dev-insecure-encryption-key-change-me-in-real-envs"
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
    s3_enabled: bool = False  # False -> FakeStorage (local/tests); True -> real S3/Spaces uploads
    s3_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "eu-central-1"
    s3_bucket: str = "minutes-audio"
    s3_access_key: str = "minutes"
    s3_secret_key: str = "minutes-secret"

    # External sub-processors
    soniox_api_key: str = ""
    # Soniox data-residency region. "eu" routes STT to api.eu / stt-rt.eu .soniox.com so audio is
    # processed in the EU — required for the EU-residency story. The API key must be from a project
    # in the matching region (an EU key only works on EU endpoints, and vice-versa).
    soniox_region: Literal["us", "eu"] = "us"
    anthropic_api_key: str = ""

    # Auth edge — capability tokens (spec v3 §15). The startup guard requires a strong,
    # non-default secret whenever app_env is not a dev env.
    auth_secret: str = DEFAULT_AUTH_SECRET
    auth_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    auth_token_ttl_s: int = 3600

    # User auth: AES-256-GCM key for per-user provider keys + session lifetimes. Web = a short
    # access JWT (cookie) + DB-backed refresh; the extension uses a device token.
    secret_key: str = DEFAULT_SECRET_KEY
    session_ttl_s: int = 900  # 15 min access JWT
    refresh_ttl_s: int = 2592000  # 30 days web refresh
    device_token_ttl_s: int = 2592000  # 30 days extension device token

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

    # Audio upload -> file transcription
    upload_max_bytes: int = 314_572_800  # ~300 MB ceiling per uploaded file
    upload_max_concurrent: int = 2  # file-transcription jobs processed at once

    # GDPR
    # ge=1 floor: a 0/negative cutoff must never purge the whole dataset.
    retention_days: int = Field(default=90, ge=1)
    require_consent: bool = False  # when true, ingest refuses meetings without granted consent

    @property
    def soniox_rt_url(self) -> str:
        """Real-time STT WebSocket URL for the configured region."""
        host = "stt-rt.eu.soniox.com" if self.soniox_region == "eu" else "stt-rt.soniox.com"
        return f"wss://{host}/transcribe-websocket"

    @property
    def soniox_file_base(self) -> str:
        """Async (file) STT REST base for the configured region."""
        host = "api.eu.soniox.com" if self.soniox_region == "eu" else "api.soniox.com"
        return f"https://{host}/v1"

    @model_validator(mode="after")
    def _enforce_strong_secret_outside_dev(self) -> Settings:
        """Fail closed: never run a non-dev env on a public-default / weak secret."""
        if self.app_env not in DEV_ENVS:
            for name, value, default in (
                ("MINUTES_AUTH_SECRET", self.auth_secret, DEFAULT_AUTH_SECRET),
                ("MINUTES_SECRET_KEY", self.secret_key, DEFAULT_SECRET_KEY),
            ):
                if value == default or len(value) < 32:
                    raise ValueError(
                        f"{name} must be a strong (>=32 byte) non-default secret when "
                        f"MINUTES_APP_ENV is '{self.app_env}'. Generate: openssl rand -hex 32"
                    )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
