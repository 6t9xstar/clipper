"""Built-in job types available in M1. Real pipeline steps (probe, proxy, transcribe, ...) arrive in M2/M3."""

from __future__ import annotations

import shutil
import time
from typing import Any

from clipper.jobs import proc
from clipper.jobs.errors import JobError
from clipper.jobs.worker import JobContext, handler


@handler("system.selftest")
def selftest(ctx: JobContext) -> dict[str, Any]:
    """Exercises the job system end to end: progress, cancellation and a real ffmpeg subprocess.

    payload: {"seconds": float = 1.0, "ffmpeg": bool = True}
    """
    seconds = float(ctx.payload.get("seconds", 1.0))
    steps = max(1, int(seconds / 0.1))
    for i in range(steps):
        time.sleep(seconds / steps)
        ctx.progress((i + 1) / steps * 0.5, "waiting")
    info: dict[str, Any] = {"slept": seconds}
    if ctx.payload.get("ffmpeg", True):
        exe = shutil.which("ffmpeg")
        if exe is None:
            raise JobError(
                "ffmpeg was not found on PATH",
                code="FFMPEG_MISSING",
                hint="Install FFmpeg and run `clipper doctor`.",
            )
        dur = float(ctx.payload.get("ffmpeg_seconds", 2))
        r = proc.run(
            [
                exe,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=s=640x360:r=30:d={dur}",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-f",
                "null",
                "-",
            ],
            should_cancel=ctx.is_cancelled,
            timeout=600,
        )
        ctx.check_cancel()
        if r.returncode != 0:
            raise JobError("ffmpeg self-test failed", code="FFMPEG_FAILED")
        info["ffmpeg"] = "ok"
    ctx.progress(1.0, "done")
    return info
