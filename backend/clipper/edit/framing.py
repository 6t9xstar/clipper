"""Vertical framing math: which part of the source frame becomes the 9:16 output.

The browser preview uses an exact TypeScript twin (frontend/src/lib/framing.ts); both are checked against the
same vectors in testdata/framing_cases.json, so the preview shows exactly the region the export will contain.
"""

from __future__ import annotations

from dataclasses import dataclass

OUT_ASPECT = 9 / 16


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float


def crop_rect(src_w: float, src_h: float, x: float = 0.5, y: float = 0.5, zoom: float = 1.0,
              aspect: float = OUT_ASPECT) -> Rect:  # fmt: skip
    """Largest `aspect` window inside the source, shrunk by `zoom`, centred on (x, y) as fractions of the
    source size, then clamped so it never leaves the frame."""
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source size must be positive")
    zoom = min(3.0, max(1.0, zoom))
    if src_w / src_h > aspect:
        cw, ch = src_h * aspect, src_h
    else:
        cw, ch = src_w, src_w / aspect
    cw, ch = cw / zoom, ch / zoom
    left = min(max(x * src_w - cw / 2, 0.0), src_w - cw)
    top = min(max(y * src_h - ch / 2, 0.0), src_h - ch)
    return Rect(round(left, 4), round(top, 4), round(cw, 4), round(ch, 4))
