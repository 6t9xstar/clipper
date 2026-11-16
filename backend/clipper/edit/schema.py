"""The `edit` object stored on each clip: captions, framing and text overlays. Validated on every write;
missing fields get defaults on read, so clients always receive the complete structure."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from clipper.edit import overlays as ov
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


class OverlayEdit(BaseModel):
    """A text overlay shown from `start` to `end` (seconds from the clip start) centred at (x, y)."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9]{1,16}$", description="Client-chosen, unique within the clip")
    style: Literal["title", "callout", "label"] = "title"
    text: str = Field(min_length=1, max_length=ov.MAX_TEXT)
    start: float = Field(ge=0, le=600)
    end: float = Field(gt=0, le=600)
    x: float = Field(default=0.5, ge=0, le=1, description="Horizontal centre, fraction of the frame width")
    y: float = Field(default=0.15, ge=0, le=1, description="Vertical centre, fraction of the frame height")

    @field_validator("text")
    @classmethod
    def _printable(cls, v: str) -> str:
        v = v.replace("\r\n", "\n").strip()
        if not v:
            raise ValueError("text is empty")
        if any(not (c.isprintable() or c == "\n") for c in v):
            raise ValueError("text contains control characters")
        if v.count("\n") >= ov.MAX_LINES:
            raise ValueError(f"at most {ov.MAX_LINES} lines")
        return v

    @model_validator(mode="after")
    def _times(self) -> OverlayEdit:
        if self.end - self.start < 0.2:
            raise ValueError("an overlay must be shown for at least 0.2 s (end > start)")
        return self


class VoiceoverEdit(BaseModel):
    """A voice track (POST /clips/{id}/voice) placed in the clip, playing from `start` (s from clip start)."""

    model_config = ConfigDict(extra="forbid")
    track_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    start: float = Field(default=0.0, ge=0, le=600)


class AudioEdit(BaseModel):
    """Levels. The export levels voice and original against each other before these offsets apply, then
    normalises the whole mix; `duck_db` turns the original down while the voice speaks (0 = off)."""

    model_config = ConfigDict(extra="forbid")
    original_db: float = Field(default=0.0, ge=-30, le=6)
    mute_original: bool = False
    voice_db: float = Field(default=0.0, ge=-12, le=12)
    duck_db: float = Field(default=-12.0, ge=-30, le=0)


class ClipEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    captions: CaptionsEdit = Field(default_factory=CaptionsEdit)
    framing: FramingEdit = Field(default_factory=FramingEdit)
    overlays: list[OverlayEdit] = Field(default_factory=list, max_length=ov.MAX_OVERLAYS)
    voiceovers: list[VoiceoverEdit] = Field(default_factory=list, max_length=10)
    audio: AudioEdit = Field(default_factory=AudioEdit)

    @field_validator("overlays")
    @classmethod
    def _unique(cls, v: list[OverlayEdit]) -> list[OverlayEdit]:
        if len({o.id for o in v}) != len(v):
            raise ValueError("overlay ids must be unique")
        return v


def normalised(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Stored edit -> full structure with defaults. Tolerates stale stored data (e.g. a removed preset)."""
    try:
        return ClipEdit.model_validate(stored or {}).model_dump()
    except ValueError:
        return ClipEdit().model_dump()
