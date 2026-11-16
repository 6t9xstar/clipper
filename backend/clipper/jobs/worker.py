"""Worker loop: claim -> run handler with heartbeat thread -> record outcome."""

from __future__ import annotations

import os
import socket
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from clipper.core.logging import log
from clipper.jobs.errors import JobCancelled, JobError, LeaseLost
from clipper.jobs.queue import Claimed, JobQueue


@dataclass
class JobContext:
    """Passed to handlers. Handlers must call check_cancel() between steps and report progress."""

    job: Claimed
    queue: JobQueue
    _cancel: threading.Event = field(default_factory=threading.Event)
    _lost: threading.Event = field(default_factory=threading.Event)
    _progress: tuple[float | None, str | None] = (None, None)

    @property
    def payload(self) -> dict[str, Any]:
        return self.job.payload

    def is_cancelled(self) -> bool:
        return self._cancel.is_set() or self._lost.is_set()

    def check_cancel(self) -> None:
        if self._lost.is_set():
            raise LeaseLost(self.job.id)
        if self._cancel.is_set():
            raise JobCancelled()

    def progress(self, fraction: float, message: str | None = None) -> None:
        self._progress = (fraction, message)  # flushed by the heartbeat thread; cheap to call often
        self.check_cancel()


Handler = Callable[[JobContext], dict[str, Any] | None]
HANDLERS: dict[str, Handler] = {}


def handler(name: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        return fn

    return deco


class Worker:
    def __init__(
        self,
        queue: JobQueue,
        *,
        poll_seconds: float = 1.0,
        handlers: dict[str, Handler] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.queue = queue
        self.poll = poll_seconds
        self.handlers = handlers if handlers is not None else HANDLERS
        self.id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        log.info("worker.start", worker=self.id, types=sorted(self.handlers))
        while not self._stop.is_set():
            if not self.run_once():
                self._stop.wait(self.poll)
        log.info("worker.stop", worker=self.id)

    def run_once(self) -> bool:
        job = self.queue.claim(self.id, types=list(self.handlers))
        if job is None:
            return False
        self._execute(job)
        return True

    def _execute(self, job: Claimed) -> None:
        ctx = JobContext(job=job, queue=self.queue)
        done = threading.Event()
        interval = max(0.2, self.queue.lease_seconds / 3)

        def beat() -> None:
            while not done.wait(interval):
                try:
                    p, m = ctx._progress
                    if self.queue.heartbeat(job, p, m):
                        ctx._cancel.set()
                except LeaseLost:
                    ctx._lost.set()
                    return
                except Exception:  # DB hiccup: keep trying; lease covers short outages
                    log.warning("worker.heartbeat_error", job=job.id, exc_info=True)

        hb = threading.Thread(target=beat, daemon=True, name=f"hb-{job.id}")
        hb.start()
        bound = log.bind(job=job.id, type=job.type, attempt=job.attempts, worker=self.id)
        bound.info("job.start")
        try:
            result = self.handlers[job.type](ctx)
            done.set()
            hb.join()
            if ctx._cancel.is_set():  # cancel arrived after the handler's last check: honour it
                self.queue.cancelled(job)
                bound.info("job.cancelled")
            elif self.queue.succeed(job, result):
                bound.info("job.succeeded")
            else:
                bound.warning("job.result_discarded_lease_lost")
        except JobCancelled:
            done.set()
            hb.join()
            self.queue.cancelled(job)
            bound.info("job.cancelled")
        except LeaseLost:
            done.set()
            hb.join()
            bound.warning("job.lease_lost")
        except JobError as e:
            done.set()
            hb.join()
            st = self.queue.fail(job, code=e.code, detail=str(e), retryable=e.retryable)
            bound.warning("job.failed", code=e.code, next_status=st)
        except Exception as e:
            done.set()
            hb.join()
            # unknown errors: retry (could be transient), keep traceback for debugging (no user content in it)
            detail = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=8)}"
            st = self.queue.fail(job, code="INTERNAL_ERROR", detail=detail, retryable=True)
            bound.error("job.crashed", next_status=st, exc_info=True)
