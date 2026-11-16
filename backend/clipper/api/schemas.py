from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clean_name(v: str | None) -> str | None:
    if v is None:
        return v
    v = " ".join(v.split())  # collapses whitespace incl. control chars like \t \n
    if not v or any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
        raise ValueError("name must be printable text")
    return v


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    _v = field_validator("name")(_clean_name)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    _v = field_validator("name")(_clean_name)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    created_at: float
    updated_at: float


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    project_id: str | None
    status: str
    attempts: int
    max_attempts: int
    progress: float
    progress_message: str | None
    cancel_requested: bool
    result: dict[str, Any] | None
    error_code: str | None
    created_at: float
    started_at: float | None
    finished_at: float | None


class JobCreate(BaseModel):
    """Only whitelisted job types may be created directly through the API."""

    type: str = Field(pattern=r"^system\.selftest$")
    payload: dict[str, Any] = Field(default_factory=dict)


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".ts", ".mts", ".m2ts", ".flv"}


def _display_name(v: str) -> str:
    """Keep only the base name, strip control characters. Display-only: never used as a path."""
    import unicodedata

    v = unicodedata.normalize("NFC", v).replace("\\", "/").rsplit("/", 1)[-1]
    v = "".join(ch for ch in v if ch.isprintable()).strip()[:255]
    if not v or v in {".", ".."}:
        raise ValueError("file name is empty")
    return v


class AssetCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(gt=0)
    rights_type: str = Field(
        pattern=r"^(own_content|licensed|permission_from_owner|public_domain_or_cc|other)$",
        description="Your basis for using this video",
    )
    rights_note: str | None = Field(default=None, max_length=1000)
    rights_confirmed: bool = Field(description="I confirm I have the right to use and edit this video")

    _v = field_validator("filename")(_display_name)

    @field_validator("rights_confirmed")
    @classmethod
    def _must_confirm(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("you must confirm you have the right to use this video")
        return v

    @field_validator("filename")
    @classmethod
    def _ext(cls, v: str) -> str:
        import os

        if os.path.splitext(v)[1].lower() not in ALLOWED_EXTENSIONS:
            raise ValueError("unsupported file type (use MP4, MOV, MKV, WebM, AVI, TS or FLV)")
        return v

    @model_validator(mode="after")
    def _note_for_other(self) -> AssetCreate:
        if self.rights_type == "other" and not (self.rights_note and self.rights_note.strip()):
            raise ValueError("rights_note is required when rights_type is 'other'")
        return self


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    display_name: str
    status: str
    size_bytes: int
    received_bytes: int
    sha256: str | None
    rights_type: str
    rights_note: str | None
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool | None
    warnings: list[str]
    media_info: dict[str, Any] | None
    import_job_id: str | None
    error_code: str | None
    error_message: str | None
    error_hint: str | None = None
    created_at: float
    upload_url: str | None = None
