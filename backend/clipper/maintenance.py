"""Periodic housekeeping: expire abandoned uploads and remove stale temp files."""

from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy import select

from clipper.core.logging import log
from clipper.core.services import Services
from clipper.db.models import Asset, AssetStatus
from clipper.media.importer import asset_prefix


def sweep(svc: Services, now: float | None = None) -> dict[str, int]:
    now = now or time.time()
    cutoff = now - svc.settings.temp_retention_hours * 3600
    expired = removed = 0
    with svc.db.session() as s:
        stale = list(
            s.scalars(select(Asset).where(Asset.status == AssetStatus.UPLOADING, Asset.updated_at < cutoff))
        )
        for a in stale:
            svc.storage.path(f"{asset_prefix(a.project_id, a.id)}/upload.part").unlink(missing_ok=True)
            a.status, a.error_code = AssetStatus.FAILED, "UPLOAD_ABANDONED"
            a.error_message = "The upload was not finished in time and was discarded."
            expired += 1
    root: Path = svc.storage.root
    for p in root.rglob(".part-*"):  # temp outputs of interrupted writes/renders
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass  # in use (Windows) - next sweep
    if expired or removed:
        log.info("maintenance.sweep", expired_uploads=expired, removed_temp_files=removed)
    return {"expired_uploads": expired, "removed_temp_files": removed}
