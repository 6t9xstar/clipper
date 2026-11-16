"""Database-backed job queue.

Design (validated in M0 spike P1b, see docs/benchmarks.md):
* Claim is ONE atomic UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING;
  Postgres adds FOR UPDATE SKIP LOCKED.
* Each claim gets a fresh lease_token. Every later write (heartbeat/progress/finish) is fenced on that token,
  so a worker whose lease expired can never overwrite the result of the worker that took over.
* A running job whose lease expired is reclaimable (crash recovery). Jobs that exhausted attempts are failed.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, or_, select, update

from clipper.core.ids import new_id
from clipper.db.models import TERMINAL, Job, JobStatus
from clipper.db.session import Database


@dataclass(frozen=True)
class Claimed:
    id: str
    type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_token: str


class JobQueue:
    def __init__(
        self,
        db: Database,
        *,
        lease_seconds: float = 60.0,
        max_attempts: int = 3,
        backoff: Sequence[float] = (30.0, 120.0, 600.0),
    ) -> None:
        self.db = db
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.backoff = tuple(backoff) or (0.0,)
        self._pg = db.engine.dialect.name == "postgresql"

    # ---------- producer side ----------
    def enqueue(
        self,
        type_: str,
        payload: dict[str, Any] | None = None,
        *,
        project_id: str | None = None,
        priority: int = 0,
        max_attempts: int | None = None,
    ) -> str:
        job = Job(
            type=type_,
            payload=payload or {},
            project_id=project_id,
            priority=priority,
            max_attempts=max_attempts or self.max_attempts,
            status=JobStatus.QUEUED,
            run_after=0.0,
        )
        with self.db.session() as s:
            s.add(job)
        return job.id

    def request_cancel(self, job_id: str) -> str | None:
        """Cancel a job and return its new status.

        Queued jobs are cancelled immediately; running jobs get a flag the worker acts on.
        """
        now = time.time()
        with self.db.session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return None
            if job.status in TERMINAL:
                return job.status
            if job.status == JobStatus.QUEUED:
                job.status, job.finished_at, job.cancel_requested = JobStatus.CANCELLED, now, True
                job.error_code = "CANCELLED"
            else:
                job.cancel_requested = True
            return job.status

    def retry(self, job_id: str) -> bool:
        """Manually re-run a failed or cancelled job (fresh attempt budget)."""
        with self.db.session() as s:
            res = s.execute(
                update(Job)
                .where(Job.id == job_id, Job.status.in_([JobStatus.FAILED, JobStatus.CANCELLED]))
                .values(
                    status=JobStatus.QUEUED,
                    attempts=0,
                    run_after=0.0,
                    cancel_requested=False,
                    error_code=None,
                    error_detail=None,
                    progress=0.0,
                    progress_message=None,
                    lease_token=None,
                    lease_until=None,
                    finished_at=None,
                    result=None,
                )
            )
            return bool(res.rowcount)  # type: ignore[attr-defined]

    # ---------- consumer side ----------
    def claim(self, worker_id: str, types: Sequence[str] | None = None) -> Claimed | None:
        now = time.time()
        self._fail_exhausted(now)
        token = new_id()
        ready = or_(
            and_(Job.status == JobStatus.QUEUED, Job.run_after <= now),
            and_(Job.status == JobStatus.RUNNING, Job.lease_until < now),  # crashed worker
        )
        sub = (
            select(Job.id)
            .where(ready, Job.attempts < Job.max_attempts, Job.cancel_requested.is_(False))
            .order_by(Job.priority.desc(), Job.created_at)
            .limit(1)
        )
        if types:
            sub = sub.where(Job.type.in_(list(types)))
        if self._pg:
            sub = sub.with_for_update(skip_locked=True)
        stmt = (
            update(Job)
            .where(Job.id == sub.scalar_subquery(), ready)
            .values(
                status=JobStatus.RUNNING,
                lease_token=token,
                lease_until=now + self.lease_seconds,
                worker_id=worker_id,
                attempts=Job.attempts + 1,
                started_at=now,
            )
            .returning(Job.id, Job.type, Job.payload, Job.attempts, Job.max_attempts)
        )
        with self.db.session() as s:
            row = s.execute(stmt).first()
        if row is None:
            return None
        return Claimed(row.id, row.type, row.payload or {}, row.attempts, row.max_attempts, token)

    def heartbeat(self, c: Claimed, progress: float | None = None, message: str | None = None) -> bool:
        """Extend the lease. Returns cancel_requested. Raises LeaseLost if we no longer own the job."""
        from clipper.jobs.errors import LeaseLost

        values: dict[str, Any] = {"lease_until": time.time() + self.lease_seconds}
        if progress is not None:
            values["progress"] = max(0.0, min(1.0, progress))
        if message is not None:
            values["progress_message"] = message[:200]
        with self.db.session() as s:
            res = s.execute(
                update(Job)
                .where(Job.id == c.id, Job.lease_token == c.lease_token, Job.status == JobStatus.RUNNING)
                .values(**values)
                .returning(Job.cancel_requested)
            ).first()
        if res is None:
            raise LeaseLost(c.id)
        return bool(res[0])

    def succeed(self, c: Claimed, result: dict[str, Any] | None = None) -> bool:
        return self._finish(c, status=JobStatus.SUCCEEDED, result=result, progress=1.0)

    def cancelled(self, c: Claimed) -> bool:
        return self._finish(c, status=JobStatus.CANCELLED, error_code="CANCELLED")

    def fail(self, c: Claimed, *, code: str, detail: str, retryable: bool) -> str | None:
        """Retryable failures go back to the queue with backoff until attempts are exhausted."""
        if retryable and c.attempts < c.max_attempts:
            delay = self.backoff[min(c.attempts - 1, len(self.backoff) - 1)]
            ok = self._finish(
                c,
                status=JobStatus.QUEUED,
                error_code=code,
                error_detail=detail,
                run_after=time.time() + delay,
                terminal=False,
            )
            return JobStatus.QUEUED if ok else None
        ok = self._finish(c, status=JobStatus.FAILED, error_code=code, error_detail=detail)
        return JobStatus.FAILED if ok else None

    def _finish(self, c: Claimed, *, status: str, terminal: bool = True, **values: Any) -> bool:
        values.update(status=status, lease_token=None, lease_until=None)
        if terminal:
            values["finished_at"] = time.time()
        with self.db.session() as s:
            res = s.execute(
                update(Job).where(Job.id == c.id, Job.lease_token == c.lease_token).values(**values)
            )
            return bool(res.rowcount)  # type: ignore[attr-defined]

    def _fail_exhausted(self, now: float) -> None:
        """Running jobs whose worker died on their last attempt can never be claimed again: fail them."""
        with self.db.session() as s:
            s.execute(
                update(Job)
                .where(
                    Job.status == JobStatus.RUNNING, Job.lease_until < now, Job.attempts >= Job.max_attempts
                )
                .values(
                    status=JobStatus.FAILED,
                    error_code="WORKER_LOST",
                    finished_at=now,
                    error_detail="The worker stopped unexpectedly on the final attempt.",
                    lease_token=None,
                    lease_until=None,
                )
            )
            # cancel requested while the worker was gone: finish the cancellation
            s.execute(
                update(Job)
                .where(Job.status == JobStatus.RUNNING, Job.lease_until < now, Job.cancel_requested.is_(True))
                .values(
                    status=JobStatus.CANCELLED,
                    error_code="CANCELLED",
                    finished_at=now,
                    lease_token=None,
                    lease_until=None,
                )
            )
