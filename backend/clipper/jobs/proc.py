"""Run external tools (ffmpeg, ffprobe) so that cancellation kills the whole process tree, on every OS."""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
from collections.abc import Callable, Sequence

import psutil

from clipper.jobs.errors import JobCancelled, JobError


def kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = [*parent.children(recursive=True), parent]
    for p in procs:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.kill()
    psutil.wait_procs(procs, timeout=5)


def run(
    argv: Sequence[str],
    *,
    should_cancel: Callable[[], bool],
    timeout: float | None = None,
    poll: float = 0.25,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run argv (never through a shell). Polls should_cancel(); kills the tree on cancel/timeout."""
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, **kwargs)  # type: ignore[call-overload]
    out: dict[str, bytes] = {}

    def _drain() -> None:  # read pipes in a thread so a chatty tool can't deadlock on a full pipe
        o, e = proc.communicate()
        out["o"], out["e"] = o, e

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    waited = 0.0
    while t.is_alive():
        t.join(poll)
        waited += poll
        if should_cancel():
            kill_tree(proc.pid)
            t.join(5)
            raise JobCancelled()
        if timeout is not None and waited > timeout:
            kill_tree(proc.pid)
            t.join(5)
            raise JobError(f"{os.path.basename(argv[0])} timed out after {timeout:.0f}s", code="TOOL_TIMEOUT")
    return subprocess.CompletedProcess(list(argv), proc.returncode, out.get("o", b""), out.get("e", b""))
