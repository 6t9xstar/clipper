"""Caption presets and word grouping: the single source of truth for the browser preview (M4) and the
burned-in render (M5). Sizes are in pixels of the 1080x1920 output frame.

Grouping happens here, not in the browser, so preview and export always show the same words on the same lines
at the same times. The renderer may differ only in font rasterisation (verified separately in M5).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

FRAME_W, FRAME_H = 1080, 1920
SENTENCE_END = re.compile(r"[.?!。？！]$")  # noqa: RUF001 - CJK sentence ends
GROUP_GAP = 0.6  # s of silence that starts a new caption
HOLD = 0.5  # s a caption may stay after its last word when nothing follows


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    description: str
    font_family: str  # CSS font stack; the first family is the intended font
    font_weight: int
    size: int  # px in the 1080x1920 frame
    uppercase: bool
    color: str
    highlight: str | None  # colour of the word being spoken; None = no word highlight
    outline: int  # px
    outline_color: str
    shadow: int  # px
    box: str | None  # background box colour (#RRGGBBAA) or None
    max_words: int
    max_chars_per_line: int
    max_lines: int
    position: float  # vertical centre of the caption block, 0 = top, 1 = bottom

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


PRESETS: dict[str, Preset] = {
    p.id: p
    for p in (
        Preset("bold", "Bold", "Big uppercase words, spoken word in yellow. Good for talking-head clips.",
               "'Arial Black', 'Arial Bold', Arial, sans-serif", 900, 84, True, "#FFFFFF", "#FFE14D",
               7, "#000000", 0, None, 3, 16, 2, 0.70),
        Preset("clean", "Clean", "Sentence case in two short lines with a soft shadow. Good for podcasts.",
               "Arial, 'Helvetica Neue', Helvetica, sans-serif", 700, 64, False, "#FFFFFF", None,
               3, "#000000", 4, None, 7, 22, 2, 0.78),
        Preset("box", "Box", "White text on a dark box, spoken word in green. Readable on busy footage.",
               "Arial, 'Helvetica Neue', Helvetica, sans-serif", 800, 66, False, "#FFFFFF", "#7CFF6B",
               0, "#000000", 0, "#000000B3", 5, 20, 2, 0.75),
        Preset("gaming", "Gaming", "Huge punchy words with a thick outline and a magenta highlight.",
               "Impact, 'Arial Black', sans-serif", 900, 104, True, "#FFFFFF", "#FF4FD8",
               9, "#000000", 0, None, 2, 12, 1, 0.62),
    )
}  # fmt: skip
DEFAULT_PRESET = "bold"


def group_words(words: Sequence[dict[str, Any]], clip_start: float, clip_end: float,
                preset: Preset) -> list[dict[str, Any]]:  # fmt: skip
    """Words fully inside [clip_start, clip_end] -> caption groups with times relative to the clip start.

    Returns [{"s", "e", "lines": [[{"w", "s", "e"}, ...], ...]}]. Words with empty text (deleted in the
    transcript editor) are skipped.
    """
    tol = 0.05
    inside: list[dict[str, Any]] = [
        {
            "w": str(w["w"]).strip(),
            "s": round(float(w["s"]) - clip_start, 3),
            "e": round(float(w["e"]) - clip_start, 3),
        }
        for w in words
        if str(w["w"]).strip() and float(w["s"]) >= clip_start - tol and float(w["e"]) <= clip_end + tol
    ]
    budget = preset.max_chars_per_line * preset.max_lines
    groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for w in inside:
        if cur:
            chars = sum(len(x["w"]) + 1 for x in cur) + len(w["w"])
            if (
                len(cur) >= preset.max_words
                or chars > budget
                or w["s"] - cur[-1]["e"] >= GROUP_GAP
                or SENTENCE_END.search(cur[-1]["w"])
            ):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    out: list[dict[str, Any]] = []
    duration = round(clip_end - clip_start, 3)
    for k, g in enumerate(groups):
        nxt = groups[k + 1][0]["s"] if k + 1 < len(groups) else None
        end = g[-1]["e"] + HOLD if nxt is None else min(nxt, g[-1]["e"] + HOLD)
        end = max(min(end, duration), g[-1]["e"])
        out.append({"s": max(0.0, g[0]["s"]), "e": round(end, 3), "lines": _lines(g, preset)})
    return out


def _lines(g: list[dict[str, Any]], p: Preset) -> list[list[dict[str, Any]]]:
    """Greedy line fill; balanced split when two lines are needed so lines have similar length."""
    text_len = sum(len(w["w"]) for w in g) + len(g) - 1
    if text_len <= p.max_chars_per_line or p.max_lines == 1 or len(g) == 1:
        return [g]
    best, best_diff = 1, float("inf")
    for k in range(1, len(g)):
        a = sum(len(w["w"]) for w in g[:k]) + k - 1
        b = sum(len(w["w"]) for w in g[k:]) + len(g) - k - 1
        if max(a, b) <= p.max_chars_per_line and abs(a - b) < best_diff:
            best, best_diff = k, abs(a - b)
    if best_diff == float("inf"):  # a single over-long word: split as evenly as possible by words
        best = len(g) // 2
    return [g[:best], g[best:]]
