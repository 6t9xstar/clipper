"""SRT and WebVTT sidecar captions from the same groups that are burned in (one cue per caption screen).

Text keeps the transcript's own capitals (platforms show sidecars in their own style)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _t(t: float, sep: str) -> str:
    ms = max(0, round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _text(g: dict[str, Any]) -> list[str]:
    # a line of only "-->" would break both formats; blank lines would end the cue
    return [" ".join(w["w"] for w in ln).replace("-->", "->").strip() or "…" for ln in g["lines"]]


def srt(groups: Sequence[dict[str, Any]]) -> str:
    out: list[str] = []
    for i, g in enumerate(groups, 1):
        out += [str(i), f"{_t(g['s'], ',')} --> {_t(g['e'], ',')}", *_text(g), ""]
    return "\n".join(out)


def vtt(groups: Sequence[dict[str, Any]]) -> str:
    out = ["WEBVTT", ""]
    for g in groups:
        text = [x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for x in _text(g)]
        out += [f"{_t(g['s'], '.')} --> {_t(g['e'], '.')}", *text, ""]
    return "\n".join(out)
