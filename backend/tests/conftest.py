from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from clipper.core.config import Settings
from clipper.db import migrate
from clipper.db.session import Database
from clipper.jobs.queue import JobQueue


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        log_json=False,
        job_lease_seconds=2.0,
        job_retry_backoff_seconds=[0.0],
        worker_poll_seconds=0.05,
    )


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    migrate.upgrade(settings.db_url)
    d = Database(settings.db_url)
    yield d
    d.dispose()


@pytest.fixture
def queue(db: Database) -> JobQueue:
    return JobQueue(db, lease_seconds=2.0, max_attempts=3, backoff=[0.0])
