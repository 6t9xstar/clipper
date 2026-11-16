"""`media.import` job: validate an uploaded file and prepare everything the editor needs.

Steps (each idempotent, so a retry after a crash resumes instead of redoing work):
  1. sha256 of the upload            (skipped if already stored)
  2. ffprobe + validation            -> MediaError (non-retryable) with a user-facing code
  3. decode check head/tail          -> catches truncated/corrupt files
  4. move upload -> source           (atomic rename)
  5. one FFmpeg pass                 -> proxy.mp4, thumbs.jpg, audio.wav
  6. waveform.json
On a MediaError the uploaded bytes are deleted (nothing usable to keep) and the asset is marked failed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from clipper.core.logging import log
from clipper.db.models import Asset, AssetStatus
from clipper.jobs.errors import JobCancelled, JobError
from clipper.jobs.worker import JobContext, handler
from clipper.media import prepare, probe
from clipper.media.errors import MediaError


def asset_prefix(project_id: str, asset_id: str) -> str:
    return f"projects/{project_id}/assets/{asset_id}"


def _sha256(path: Path, ctx: JobContext, lo: float, hi: float) -> str:
    h = hashlib.sha256()
    total = max(1, path.stat().st_size)
    done = 0
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            h.update(chunk)
            done += len(chunk)
            ctx.progress(lo + (hi - lo) * done / total, "Checking file")
    return h.hexdigest()


def _update(ctx: JobContext, asset_id: str, **values: Any) -> None:
    with ctx.svc.db.session() as s:
        a = s.get(Asset, asset_id)
        if a is not None:
            for k, v in values.items():
                setattr(a, k, v)


@handler("media.import")
def import_asset(ctx: JobContext) -> dict[str, Any]:
    asset_id = str(ctx.payload["asset_id"])
    svc = ctx.svc
    with svc.db.session() as s:
        a = s.get(Asset, asset_id)
        if a is None:
            raise JobError("asset was deleted", code="ASSET_GONE")
        project_id, sha = a.project_id, a.sha256
    prefix = asset_prefix(project_id, asset_id)
    upload = svc.storage.path(f"{prefix}/upload.part")
    source = svc.storage.path(f"{prefix}/source")
    work = svc.storage.path(f"{prefix}/derived")
    src = source if source.exists() else upload
    if not src.exists():
        raise JobError("uploaded file is missing", code="UPLOAD_MISSING", hint="Upload the file again.")

    try:
        if sha is None:
            sha = _sha256(src, ctx, 0.0, 0.08)
            _update(ctx, asset_id, sha256=sha)
        ctx.progress(0.08, "Reading video details")
        info = probe.analyse(probe.ffprobe_json(src, ctx.is_cancelled),
                             max_duration_s=svc.settings.max_duration_seconds)  # fmt: skip
        ctx.check_cancel()
        ctx.progress(0.10, "Checking the video plays")
        probe.decode_check(src, info, ctx.is_cancelled)
    except MediaError as e:
        log.info("media.rejected", asset=asset_id, code=e.code, detail=e.detail)
        _update(ctx, asset_id, status=AssetStatus.FAILED, error_code=e.code, error_message=str(e))
        src.unlink(missing_ok=True)  # unusable bytes: free the disk now
        raise

    if src == upload:
        os.replace(upload, source)
    _update(ctx, asset_id, duration_ms=info.duration_ms, width=info.width, height=info.height, fps=info.fps,
            has_audio=info.has_audio, warnings=info.warnings,
            media_info={"media": info.to_json()})  # fmt: skip

    try:
        ctx.progress(0.12, "Preparing preview")
        outs = prepare.run_prepare(
            source, info, work, should_cancel=ctx.is_cancelled,
            on_progress=lambda f: ctx.progress(0.12 + 0.80 * f, "Preparing preview"),
        )  # fmt: skip
        peaks: list[int] = []
        wf = work / "waveform.json"
        if "audio" in outs and not wf.exists():
            ctx.progress(0.93, "Drawing waveform")
            peaks = prepare.waveform(outs["audio"], should_cancel=ctx.is_cancelled)
            with svc.storage.open_write(f"{prefix}/derived/waveform.json") as fh:
                fh.write(
                    json.dumps(
                        {"rate": prepare.WAVEFORM_RATE, "peaks": peaks}, separators=(",", ":")
                    ).encode()
                )
    except MediaError as e:
        _update(ctx, asset_id, status=AssetStatus.FAILED, error_code=e.code, error_message=str(e))
        raise
    except JobCancelled:
        raise

    ts = prepare.thumb_spec(info)
    pw, ph = prepare.proxy_size(info)
    _update(ctx, asset_id, status=AssetStatus.READY, error_code=None, error_message=None,
            media_info={"media": info.to_json(), "thumbs": ts.to_json(),
                        "proxy": {"width": pw, "height": ph, "fps": min(info.fps, prepare.PROXY_MAX_FPS)},
                        "waveform": {"rate": prepare.WAVEFORM_RATE} if info.has_audio else None})  # fmt: skip
    ctx.progress(1.0, "Ready")
    return {"asset_id": asset_id, "duration_ms": info.duration_ms, "warnings": info.warnings}
