"""Application settings. Every field is documented in docs/configuration.md and .env.example."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLIPPER_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("data"), description="Root for DB, media, artifacts, temp files")
    database_url: str | None = Field(default=None, description="SQLAlchemy URL; default SQLite in data_dir")

    host: str = Field(default="127.0.0.1", description="Bind address. Non-loopback requires auth (v1.0)")
    port: int = Field(default=8765, ge=1, le=65535)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    # job system
    job_lease_seconds: float = Field(default=60.0, gt=1)
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_retry_backoff_seconds: list[float] = Field(default_factory=lambda: [30.0, 120.0, 600.0])
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    worker_concurrency: int = Field(default=1, ge=1, le=64)

    # upload / media limits (enforced in M2)
    max_upload_bytes: int = Field(default=20 * 1024**3, gt=0)
    max_duration_seconds: int = Field(default=4 * 3600, gt=0)
    temp_retention_hours: int = Field(default=24, ge=1)

    @field_validator("data_dir")
    @classmethod
    def _abs(cls, v: Path) -> Path:
        return v.expanduser().resolve()

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'clipper.db').as_posix()}"

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
