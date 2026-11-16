"""Job error types. Handlers raise these to tell the queue whether a failure is worth retrying."""

from __future__ import annotations


class JobError(Exception):
    """Base error with a stable machine code and a user-facing hint."""

    code = "JOB_FAILED"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None, hint: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code
        self.hint = hint


class RetryableJobError(JobError):
    code = "JOB_TRANSIENT_ERROR"
    retryable = True


class JobCancelled(Exception):
    """Raised inside a handler when cancellation was requested."""


class LeaseLost(Exception):
    """This worker no longer owns the job (lease expired and someone else claimed it)."""
