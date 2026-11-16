"""M1 acceptance: a real `clipper worker` is hard-killed mid-job; another worker finishes it exactly once."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import psutil
import pytest

from clipper.core.config import Settings
from clipper.db.models import Job
from clipper.db.session import Database
from clipper.jobs.queue import JobQueue

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def spawn_worker(settings: Settings) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "CLIPPER_DATA_DIR": str(settings.data_dir),
        "CLIPPER_JOB_LEASE_SECONDS": "2",
        "CLIPPER_WORKER_POLL_SECONDS": "0.1",
        "CLIPPER_JOB_RETRY_BACKOFF_SECONDS": "[0]",
        "CLIPPER_LOG_JSON": "true",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "clipper.cli", "worker"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def get(db: Database, jid: str) -> Job:
    with db.session() as s:
        j = s.get(Job, jid)
        assert j is not None
        return j


def test_hard_killed_worker_job_is_recovered(settings: Settings, db: Database) -> None:
    q = JobQueue(db, lease_seconds=2, max_attempts=3)
    jid = q.enqueue("system.selftest", {"seconds": 6, "ffmpeg_seconds": 2})  # killed ~1 s into the 6 s phase
    w1 = spawn_worker(settings)
    w2: subprocess.Popen[bytes] | None = None
    try:
        end = time.time() + 30
        while time.time() < end and get(db, jid).status != "running":
            time.sleep(0.05)
        assert get(db, jid).status == "running", "worker never picked up the job"
        time.sleep(1.0)
        # SIGKILL-equivalent: no cleanup code runs in the worker
        for p in [*psutil.Process(w1.pid).children(recursive=True), psutil.Process(w1.pid)]:
            p.kill()
        w1.wait(10)
        assert get(db, jid).status == "running"  # orphaned, lease still in the DB

        w2 = spawn_worker(settings)
        end = time.time() + 90
        while time.time() < end and get(db, jid).status not in ("succeeded", "failed"):
            time.sleep(0.1)
        j = get(db, jid)
        assert j.status == "succeeded", (j.status, j.error_code, j.error_detail)
        assert j.attempts == 2
        assert j.result == {"slept": 6.0, "ffmpeg": "ok"}
    finally:
        for p in (w1, w2):
            if p and p.poll() is None:
                p.kill()
                p.wait(10)
