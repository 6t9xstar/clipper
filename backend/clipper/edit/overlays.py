"""Text overlays (chapter titles, callouts, labels): styles and layout.

Layout is computed here only, like caption line-breaking: the editor preview asks the server
(`POST /api/v1/overlays/layout`) and the renderer uses the same function, so wrapping, shrink-to-fit and the
clamping to the frame edges are identical in both. Sizes are pixels of the 1080x1920 output frame.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from clipper.edit.captions import FRAME_H, FRAME_W, SIDE_MARGIN, Preset, fit, line_centres
from clipper.render import fontinfo

TOP_BOTTOM_MARGIN = 40  # px kept free above and below an overlay block
MIN_SHOWN = 0.05  # s; shorter remains after trimming the clip are dropped

# Overlay styles reuse the caption Preset type (font, size, colours, box, wrapping); the caption-only fields
# (highlight, max_words, position) are fixed.
STYLES: dict[str, Preset] = {
    p.id: p
    for p in (
        Preset("title", "Chapter title", "Large uppercase heading on a dark box, e.g. a chapter or topic.",
               "Montserrat-Black.ttf", 76, True, "#FFFFFF", None,
               0, "#000000", 0, "#000000CC", 18, 1.75, 99, 16, 3, 0.15),
        Preset("callout", "Callout", "Dark text on a yellow box to point something out.",
               "Montserrat-ExtraBold.ttf", 56, False, "#111111", None,
               0, "#000000", 0, "#FFE14DFF", 14, 1.8, 99, 20, 4, 0.30),
        Preset("label", "Label", "White text with an outline, no box. For names, places, sources.",
               "Montserrat-Bold.ttf", 52, False, "#FFFFFF", None,
               5, "#000000", 0, None, 0, 1.2, 99, 24, 4, 0.40),
    )
}  # fmt: skip
MAX_OVERLAYS = 20
MAX_TEXT = 120
MAX_LINES = 4  # explicit line breaks the user may type


def _greedy(words: list[str], limit: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > limit:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        lines.append(cur)
    return lines


def wrap(text: str, p: Preset) -> list[str]:
    """User line breaks are kept. Each paragraph gets as few lines as the style's characters-per-line allow,
    balanced: the narrowest width that still needs no extra line (so no short last line). A single word
    longer than the limit stays on its own line and is shrunk by `fit`."""
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split()
        if not words:
            continue
        best = _greedy(words, p.max_chars_per_line)
        for limit in range(p.max_chars_per_line - 1, 0, -1):
            trial = _greedy(words, limit)
            if len(trial) > len(best):
                break
            best = trial
        lines += best
    return [ln.upper() if p.uppercase else ln for ln in lines]


def layout(o: dict[str, Any], duration: float) -> dict[str, Any] | None:
    """One overlay -> what to draw: lines, per-line scale, centre x and line centres y (px), clip times.

    None when nothing remains to show (empty text, or the clip was trimmed so the overlay is outside it)."""
    p = STYLES[o["style"]]
    s = max(0.0, float(o["start"]))
    e = min(float(o["end"]), duration)
    lines = wrap(str(o["text"]), p)
    if not lines or e - s < MIN_SHOWN:
        return None
    fits = [fit(t, p) for t in lines]
    m = fontinfo.metrics(p.font_file)
    pad = p.outline + (p.box_padding if p.box else 0)
    half_w = max(m.width(t, p.size) * f for t, f in zip(lines, fits, strict=True)) / 2 + pad
    lo, hi = SIDE_MARGIN + half_w, FRAME_W - SIDE_MARGIN - half_w
    x = FRAME_W / 2 if lo > hi else min(max(float(o["x"]) * FRAME_W, lo), hi)
    pitch = p.line_height * p.size
    half_h = len(lines) * pitch / 2
    lo, hi = TOP_BOTTOM_MARGIN + half_h, FRAME_H - TOP_BOTTOM_MARGIN - half_h
    y = FRAME_H / 2 if lo > hi else min(max(float(o["y"]) * FRAME_H, lo), hi)
    return {
        "id": o["id"],
        "style": p.id,
        "s": round(s, 3),
        "e": round(e, 3),
        "lines": lines,
        "fit": fits,
        "x": round(x, 1),
        "ys": [round(c, 1) for c in line_centres(len(lines), p, y / FRAME_H)],
    }


def layout_all(overlays: Sequence[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    return [lo for o in overlays if (lo := layout(o, duration)) is not None]
