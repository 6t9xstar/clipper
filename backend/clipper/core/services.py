"""Process-wide service container shared by the API and workers."""

from __future__ import annotations

from clipper.core.config import Settings
from clipper.db.session import Database
from clipper.jobs.queue import JobQueue
from clipper.storage import LocalStorage


class Services:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_url)
        self.storage = LocalStorage(settings.data_dir / "storage")
        self.queue = JobQueue(
            self.db,
            lease_seconds=settings.job_lease_seconds,
            max_attempts=settings.job_max_attempts,
            backoff=settings.job_retry_backoff_seconds,
        )

    def close(self) -> None:
        self.db.dispose()
