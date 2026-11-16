"""Shot-cut detection with FFmpeg's built-in `scdet` filter, on the 540p proxy at 10 fps / 160 px wide.

Chosen over PySceneDetect for M3: no extra dependency (OpenCV), runs where FFmpeg runs, and the accuracy
needed here (snap clip edges near hard cuts, measure visual pace) is modest. Gradual transitions are not
detected, nor are cuts between shots of equal brightness (scdet compares luma).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path

from clipper.jobs import proc
from clipper.media.probe import SAFE_INPUT_ARGS, tool

THRESHOLD = 12.0  # scdet score 0-100; validated on synthetic hard cuts (docs/benchmarks.md)


def detect_cuts(
    video: Path, duration_s: float, *, should_cancel: Callable[[], bool], on_progress: Callable[[float], None]
) -> list[float]:
    vf = (
        f"fps=10,scale=160:-2,scdet=threshold={THRESHOLD}:sc_pass=1,"
        "metadata=mode=print:key=lavfi.scd.time:file=-"
    )
    argv = [
        tool("ffmpeg"),
        "-v",
        "error",
        "-nostdin",
        *SAFE_INPUT_ARGS,
        "-i",
        str(video),
        "-an",
        "-vf",
        vf,
        "-f",
        "null",
        "-",
    ]
    cuts: list[float] = []

    def line(s: str) -> None:
        if s.startswith("lavfi.scd.time="):
            with contextlib.suppress(ValueError):
                t = float(s.split("=", 1)[1])
                cuts.append(t)
                on_progress(min(1.0, t / max(duration_s, 1e-6)))

    r = proc.run(argv, should_cancel=should_cancel, timeout=max(600.0, duration_s), on_stdout_line=line)
    if r.returncode != 0:
        from clipper.jobs.errors import RetryableJobError

        raise RetryableJobError("scene detection failed", code="SCENE_DETECTION_FAILED")
    return sorted(set(round(c, 2) for c in cuts))
