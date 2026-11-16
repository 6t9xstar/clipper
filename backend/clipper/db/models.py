"""ORM models. Schema changes go through Alembic migrations only (never create_all in production paths)."""

from __future__ import annotations

import enum
import time
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from clipper.core.ids import new_id


class Base(DeclarativeBase):
    pass


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AssetStatus(enum.StrEnum):
    UPLOADING = "uploading"  # bytes still arriving (resumable)
    PROCESSING = "processing"  # import job: validate, hash, proxy, waveform, thumbnails
    READY = "ready"
    FAILED = "failed"


class RightsType(enum.StrEnum):
    """The user's declared basis for using the source video (shown on the asset and in exports)."""

    OWN_CONTENT = "own_content"
    LICENSED = "licensed"
    PERMISSION_FROM_OWNER = "permission_from_owner"
    PUBLIC_DOMAIN_OR_CC = "public_domain_or_cc"
    OTHER = "other"


class Asset(Base):
    """An imported source video. `display_name` is the user's file name: UI only, never used as a path."""

    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default=AssetStatus.UPLOADING)
    size_bytes: Mapped[int] = mapped_column(BigInteger)  # declared size; upload must match exactly
    received_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rights_type: Mapped[str] = mapped_column(String(32))
    rights_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    rights_confirmed_at: Mapped[float] = mapped_column(Float)
    # media facts (from ffprobe, normalised; see clipper.media.probe)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)  # display size, after rotation
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_audio: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    media_info: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    import_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    analysis_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class Transcript(Base):
    """Transcript metadata; the words live in storage (derived/transcript.json) because they can be large."""

    __tablename__ = "transcripts"
    asset_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(16))
    language_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str] = mapped_column(String(64))
    device: Mapped[str] = mapped_column(String(16))
    word_count: Mapped[int] = mapped_column(Integer)
    seconds_taken: Mapped[float] = mapped_column(Float)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class ClipStatus(enum.StrEnum):
    SUGGESTED = "suggested"
    APPROVED = "approved"
    REJECTED = "rejected"


class Clip(Base):
    """A time range of a source video. AI suggestions and manual clips share this table.

    `version` increments on every change (optimistic concurrency: PATCH requires If-Match).
    """

    __tablename__ = "clips"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str] = mapped_column(String(32), ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(16))  # "ai" | "manual"
    status: Mapped[str] = mapped_column(String(16), default=ClipStatus.SUGGESTED)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    features: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    edit: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # captions/layout (M4/M5)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class Job(Base):
    """A unit of background work. The table *is* the queue (see clipper.jobs.queue)."""

    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[float] = mapped_column(Float, default=0.0)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    progress_message: Mapped[str | None] = mapped_column(String(200), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("ix_jobs_claim", "status", "run_after", "priority", "created_at"),)
