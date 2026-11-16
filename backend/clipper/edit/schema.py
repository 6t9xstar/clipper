"""The `edit` object stored on each clip: captions and framing. Validated on every write; missing fields get
defaults on read, so clients always receive the complete structure."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from clipper.edit.captions import DEFAULT_PRESET, PRESETS


class CaptionsEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    preset: str = DEFAULT_PRESET
    position: float | None = Field(
        default=None, ge=0.05, le=0.95, description="Vertical centre; null = preset"
    )

    @field_validator("preset")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in PRESETS:
            raise ValueError(f"unknown caption preset; use one of {sorted(PRESETS)}")
        return v


class FramingEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["crop", "fit"] = Field(
        default="crop", description="crop = fill 9:16; fit = whole frame on blur"
    )
    x: float = Field(default=0.5, ge=0, le=1, description="Horizontal centre of the crop (fraction of width)")
    y: float = Field(default=0.5, ge=0, le=1, description="Vertical centre of the crop (fraction of height)")
    zoom: float = Field(default=1.0, ge=1, le=3)


class ClipEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    captions: CaptionsEdit = Field(default_factory=CaptionsEdit)
    framing: FramingEdit = Field(default_factory=FramingEdit)


def normalised(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Stored edit -> full structure with defaults. Tolerates stale stored data (e.g. a removed preset)."""
    try:
        return ClipEdit.model_validate(stored or {}).model_dump()
    except ValueError:
        return ClipEdit().model_dump()
