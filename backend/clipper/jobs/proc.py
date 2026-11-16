"""Run external tools (ffmpeg, ffprobe) so that cancellation kills the whole process tree, on every OS.

Memory is bounded no matter how much a tool prints: stderr keeps only the last 64 KiB, and stdout is either
streamed line by line to a callback or capped at `max_stdout` bytes.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Sequence
from typing import IO

import psutil

from clipper.jobs.errors import JobCancelled, JobError

STDERR_TAIL = 64 * 1024


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
    on_stdout_line: Callable[[str], None] | None = None,
    max_stdout: int = 32 * 1024 * 1024,
) -> subprocess.CompletedProcess[bytes]:
    """Run argv (never through a shell). Polls should_cancel(); kills the tree on cancel/timeout."""
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(  # type: ignore[call-overload]
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        **kwargs,
    )
    out = bytearray()
    err: deque[bytes] = deque()
    err_len = [0]
    overflow = [False]

    def read_out(fh: IO[bytes]) -> None:
        for line in iter(fh.readline, b""):
            if on_stdout_line is not None:
                on_stdout_line(line.decode("utf-8", "replace").rstrip("\r\n"))
            elif len(out) + len(line) <= max_stdout:
                out.extend(line)
            else:
                overflow[0] = True

    def read_err(fh: IO[bytes]) -> None:
        for chunk in iter(lambda: fh.read(4096), b""):
            err.append(chunk)
            err_len[0] += len(chunk)
            while err_len[0] > STDERR_TAIL and len(err) > 1:
                err_len[0] -= len(err.popleft())

    readers = [
        threading.Thread(target=read_out, args=(proc.stdout,), daemon=True),
        threading.Thread(target=read_err, args=(proc.stderr,), daemon=True),
    ]
    for r in readers:
        r.start()
    waited = 0.0
    while proc.poll() is None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(poll)
        waited += poll
        if proc.poll() is not None:
            break
        if should_cancel():
            kill_tree(proc.pid)
            raise JobCancelled()
        if timeout is not None and waited > timeout:
            kill_tree(proc.pid)
            raise JobError(f"{os.path.basename(argv[0])} timed out after {timeout:.0f}s", code="TOOL_TIMEOUT")
    for r in readers:
        r.join(10)
    if overflow[0]:
        raise JobError(f"{os.path.basename(argv[0])} produced too much output", code="TOOL_OUTPUT_TOO_LARGE")
    return subprocess.CompletedProcess(list(argv), proc.returncode, bytes(out), b"".join(err))
