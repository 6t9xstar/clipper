from __future__ import annotations

import shutil
import threading
import time
from typing import Any

import pytest
from sqlalchemy import update

from clipper.db.models import Job
from clipper.db.session import Database
from clipper.jobs import builtin  # noqa: F401
from clipper.jobs.errors import JobError, LeaseLost, RetryableJobError
from clipper.jobs.queue import JobQueue
from clipper.jobs.worker import HANDLERS, JobContext, Worker


def status(db: Database, jid: str) -> Job:
    with db.session() as s:
        j = s.get(Job, jid)
        assert j is not None
        return j


def expire_lease(db: Database, jid: str) -> None:
    with db.session() as s:
        s.execute(update(Job).where(Job.id == jid).values(lease_until=time.time() - 1))


def test_claim_success_cycle(queue: JobQueue, db: Database) -> None:
    jid = queue.enqueue("t", {"a": 1})
    c = queue.claim("w1")
    assert c and c.id == jid and c.payload == {"a": 1} and c.attempts == 1
    assert queue.claim("w2") is None  # leased
    assert queue.succeed(c, {"ok": True})
    j = status(db, jid)
    assert (j.status, j.result, j.progress) == ("succeeded", {"ok": True}, 1.0)


def test_priority_then_fifo(queue: JobQueue) -> None:
    a = queue.enqueue("t")
    b = queue.enqueue("t", priority=5)
    c = queue.enqueue("t")
    order = [queue.claim("w").id for _ in range(3)]  # type: ignore[union-attr]
    assert order == [b, a, c]


def test_type_filter(queue: JobQueue) -> None:
    queue.enqueue("render")
    assert queue.claim("w", types=["transcribe"]) is None
    assert queue.claim("w", types=["render"]) is not None


def test_expired_lease_is_reclaimed_and_old_owner_is_fenced(queue: JobQueue, db: Database) -> None:
    jid = queue.enqueue("t")
    old = queue.claim("w1")
    assert old
    expire_lease(db, jid)
    new = queue.claim("w2")
    assert new and new.id == jid and new.attempts == 2
    assert not queue.succeed(old, {"from": "old"})  # fenced
    with pytest.raises(LeaseLost):
        queue.heartbeat(old)
    assert queue.succeed(new, {"from": "new"})
    assert status(db, jid).result == {"from": "new"}


def test_retryable_failure_backs_off_then_fails(db: Database) -> None:
    q = JobQueue(db, lease_seconds=2, max_attempts=2, backoff=[0.3])
    jid = q.enqueue("t")
    c = q.claim("w")
    assert c
    assert q.fail(c, code="X", detail="d", retryable=True) == "queued"
    assert q.claim("w") is None  # still backing off
    time.sleep(0.35)
    c2 = q.claim("w")
    assert c2 and c2.attempts == 2
    assert q.fail(c2, code="X", detail="d", retryable=True) == "failed"
    assert status(db, jid).status == "failed"


def test_non_retryable_fails_immediately(queue: JobQueue, db: Database) -> None:
    jid = queue.enqueue("t")
    c = queue.claim("w")
    assert c
    assert queue.fail(c, code="BAD_INPUT", detail="nope", retryable=False) == "failed"
    assert status(db, jid).error_code == "BAD_INPUT"


def test_crash_on_last_attempt_marks_failed(db: Database) -> None:
    q = JobQueue(db, lease_seconds=2, max_attempts=1)
    jid = q.enqueue("t")
    assert q.claim("w")
    expire_lease(db, jid)
    assert q.claim("w") is None
    j = status(db, jid)
    assert (j.status, j.error_code) == ("failed", "WORKER_LOST")


def test_cancel_queued_and_retry(queue: JobQueue, db: Database) -> None:
    jid = queue.enqueue("t")
    assert queue.request_cancel(jid) == "cancelled"
    assert queue.claim("w") is None
    assert queue.retry(jid)
    assert queue.claim("w") is not None
    assert queue.request_cancel("0" * 32) is None


def run_worker_in_thread(q: JobQueue, handlers: dict[str, Any]) -> tuple[Worker, threading.Thread]:
    w = Worker(q, poll_seconds=0.05, handlers=handlers)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    return w, t


def wait_for(db: Database, jid: str, statuses: set[str], timeout: float = 30) -> Job:
    end = time.time() + timeout
    while time.time() < end:
        j = status(db, jid)
        if j.status in statuses:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {status(db, jid).status}")


def test_worker_runs_handlers_and_maps_errors(db: Database) -> None:
    q = JobQueue(db, lease_seconds=1, max_attempts=2, backoff=[0.0])

    def ok(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0.5, "half")
        return {"x": ctx.payload["x"] * 2}

    def bad(ctx: JobContext) -> None:
        raise JobError("unsupported codec", code="MEDIA_UNSUPPORTED_CODEC")

    calls = {"n": 0}

    def flaky(ctx: JobContext) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryableJobError("disk busy")
        return {"n": calls["n"]}

    def boom(ctx: JobContext) -> None:
        raise ZeroDivisionError("bug")

    w, t = run_worker_in_thread(q, {"ok": ok, "bad": bad, "flaky": flaky, "boom": boom})
    try:
        j_ok, j_bad, j_flaky, j_boom = (q.enqueue(n, {"x": 21}) for n in ("ok", "bad", "flaky", "boom"))
        assert wait_for(db, j_ok, {"succeeded"}).result == {"x": 42}
        b = wait_for(db, j_bad, {"failed"})
        assert b.error_code == "MEDIA_UNSUPPORTED_CODEC" and b.attempts == 1
        f = wait_for(db, j_flaky, {"succeeded"})
        assert f.attempts == 2 and f.result == {"n": 2}
        bo = wait_for(db, j_boom, {"failed"})
        assert (
            bo.error_code == "INTERNAL_ERROR"
            and bo.attempts == 2
            and "ZeroDivisionError" in (bo.error_detail or "")
        )
    finally:
        w.stop()
        t.join(5)


def test_long_job_keeps_lease_via_heartbeat(db: Database) -> None:
    q = JobQueue(db, lease_seconds=0.6, max_attempts=1)

    def slow(ctx: JobContext) -> dict[str, Any]:
        for i in range(20):  # 2 s >> 0.6 s lease
            time.sleep(0.1)
            ctx.progress(i / 20)
        return {}

    w, t = run_worker_in_thread(q, {"slow": slow})
    w2, t2 = run_worker_in_thread(q, {"slow": slow})  # would steal the job if heartbeats failed
    try:
        jid = q.enqueue("slow")
        j = wait_for(db, jid, {"succeeded", "failed"})
        assert j.status == "succeeded" and j.attempts == 1
    finally:
        for x in (w, w2):
            x.stop()
        t.join(5)
        t2.join(5)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_cancel_running_job_kills_ffmpeg(db: Database) -> None:
    import psutil

    q = JobQueue(db, lease_seconds=0.6, max_attempts=3)
    w, t = run_worker_in_thread(q, {"system.selftest": HANDLERS["system.selftest"]})
    try:
        jid = q.enqueue("system.selftest", {"seconds": 0.1, "ffmpeg_seconds": 3600})
        end = time.time() + 20
        ff: list[psutil.Process] = []
        while time.time() < end and not ff:
            ff = [
                p
                for p in psutil.process_iter(["name", "cmdline"])
                if (p.info["name"] or "").lower().startswith("ffmpeg")
                and "d=3600" in " ".join(p.info["cmdline"] or [])
            ]
            time.sleep(0.1)
        assert ff, "ffmpeg never started"
        t0 = time.time()
        assert q.request_cancel(jid) == "running"
        j = wait_for(db, jid, {"cancelled", "failed", "succeeded"}, timeout=15)
        assert j.status == "cancelled"
        assert time.time() - t0 < 5
        assert not any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in ff)
    finally:
        w.stop()
        t.join(5)
