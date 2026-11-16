from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
