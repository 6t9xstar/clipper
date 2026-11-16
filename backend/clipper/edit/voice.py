"""Voiceover placement and ducking maths, shared by the export (an FFmpeg `volume` expression) and the editor
preview (frontend/src/lib/ducking.ts, checked against the same vectors in testdata/ducking_cases.json).

A voice track's speech segments are measured once when it is uploaded (clipper.media.voice). Placed in a clip
at `start`, they become clip-time segments; while any of them is active the original sound is turned down by
`duck_db`, ramping down ATTACK seconds before the voice and back up over RELEASE seconds after it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

ATTACK = 0.12  # s: duck starts this long before the voice (we know the future, so no clipped first word)
RELEASE = 0.35  # s: and recovers over this long after it
MERGE_GAP = ATTACK + RELEASE + 0.3  # s: pauses shorter than this keep the music down (no pumping)
MAX_SEGMENTS = 400


def placed_segments(placements: Iterable[tuple[float, Sequence[Sequence[float]]]], duration: float,
                    ) -> list[tuple[float, float]]:  # fmt: skip
    """[(start in clip, track speech segments)] -> merged clip-time segments inside [0, duration]."""
    segs = sorted(
        (max(0.0, start + a), min(duration, start + b))
        for start, track in placements
        for a, b in track
        if start + b > 0 and start + a < duration
    )
    out: list[tuple[float, float]] = []
    for a, b in segs:
        if b <= a:
            continue
        if out and a - out[-1][1] < MERGE_GAP:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    out = [(round(a, 3), round(b, 3)) for a, b in out]
    while len(out) > MAX_SEGMENTS:  # absurdly choppy input: merge the closest pairs until it is bounded
        k = min(range(len(out) - 1), key=lambda i: out[i + 1][0] - out[i][1])
        out[k : k + 2] = [(out[k][0], out[k + 1][1])]
    return out


def coverage(segments: Sequence[tuple[float, float]], duration: float) -> float:
    """Fraction of the clip with the user's voice (merged segments never overlap)."""
    return round(sum(b - a for a, b in segments) / duration, 4) if duration > 0 else 0.0


def _ramp(t: float, a: float, b: float) -> float:
    up = min(1.0, max(0.0, (t - (a - ATTACK)) / ATTACK))
    down = min(1.0, max(0.0, ((b + RELEASE) - t) / RELEASE))
    return up * down


def duck_gain(t: float, segments: Sequence[tuple[float, float]], duck_db: float) -> float:
    """Linear gain of the original sound at clip time t (1 = untouched). Reference for the expression."""
    if duck_db >= 0 or not segments:
        return 1.0
    g = 10 ** (duck_db / 20)
    d = max((_ramp(t, a, b) for a, b in segments), default=0.0)
    return 1 - (1 - g) * d


def duck_expr(segments: Sequence[tuple[float, float]], duck_db: float) -> str | None:
    """FFmpeg `volume` expression equal to duck_gain (evaluated per audio frame); None = no ducking.

    The max over segments is a balanced tree so the parser's nesting stays shallow (log2 of the count)."""
    if duck_db >= 0 or not segments:
        return None
    terms = [
        f"clip((t-({a - ATTACK:.3f}))/{ATTACK},0,1)*clip(({b + RELEASE:.3f}-t)/{RELEASE},0,1)"
        for a, b in segments
    ]
    while len(terms) > 1:
        terms = [f"max({terms[i]},{terms[i + 1]})" if i + 1 < len(terms) else terms[i]
                 for i in range(0, len(terms), 2)]  # fmt: skip
    g = 10 ** (duck_db / 20)
    return f"1-{1 - g:.5f}*{terms[0]}"
