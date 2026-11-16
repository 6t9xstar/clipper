"""Render & export endpoints (M5).

POST   /clips/{id}/exports            render the clip as saved (optional If-Match: the clip version you see)
GET    /clips/{id}/exports            this clip's exports, newest first
GET    /projects/{id}/exports         every export in the project (job history)
GET    /exports/{id}                  one export (status, checks, files)
GET    /exports/{id}/files/{kind}     mp4 | srt | vtt | json   (?download=1 for a "save as" file name)
DELETE /exports/{id}                  cancel if running, then delete files and record
GET    /export-qualities
GET    /clips/{id}/captions.srt|.vtt  sidecar captions of the clip as saved, without rendering
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select

from clipper.api.assets import Svc, _get, _id
from clipper.api.clips import _bounds, _expected_version, _load, _read_transcript
from clipper.api.errors import ApiError
from clipper.api.schemas import ExportCreate, ExportOut, Page
from clipper.core.services import Services
from clipper.db.models import TERMINAL, AssetStatus, Clip, Export, ExportStatus, Job, JobStatus, VoiceTrack
from clipper.edit import voice as vo
from clipper.edit.captions import PRESETS, group_words
from clipper.edit.overlays import layout_all as overlay_layout
from clipper.edit.schema import normalised
from clipper.render import subs
from clipper.render.export import export_prefix
from clipper.render.graph import QUALITIES

router = APIRouter(prefix="/api/v1")

KINDS = {"mp4": "video/mp4", "srt": "application/x-subrip", "vtt": "text/vtt", "json": "application/json"}
EXPORT_MARGIN = 512 * 1024**2


def reconcile(svc: Services, e: Export) -> Export:
    """Reflect a job that ended without updating its export (crash on last attempt, cancelled queued)."""
    if e.status not in (ExportStatus.QUEUED, ExportStatus.RENDERING) or not e.job_id:
        return e
    with svc.db.session() as s:
        job = s.get(Job, e.job_id)
        if job is None or job.status not in TERMINAL or job.status == JobStatus.SUCCEEDED:
            return e
        row = s.get(Export, e.id)
        if row is None or row.status not in (ExportStatus.QUEUED, ExportStatus.RENDERING):
            return e
        if job.status == JobStatus.CANCELLED:
            row.status = ExportStatus.CANCELLED
        else:
            row.status = ExportStatus.FAILED
            row.error_code = job.error_code or "RENDER_FAILED"
            row.error_message = "The export failed. Try again; if it keeps failing, report the job details."
        row.finished_at = job.finished_at
        s.flush()
        return row


def out(svc: Services, e: Export) -> ExportOut:
    e = reconcile(svc, e)
    progress, message = None, None
    if e.status in (ExportStatus.QUEUED, ExportStatus.RENDERING) and e.job_id:
        with svc.db.session() as s:
            job = s.get(Job, e.job_id)
            if job is not None:
                progress, message = job.progress, job.progress_message
    return ExportOut.model_validate(e).model_copy(update={"progress": progress, "progress_message": message})


def _export(svc: Services, export_id: str) -> Export:
    with svc.db.session() as s:
        e = s.get(Export, _id(export_id))
    if e is None:
        raise ApiError(404, "EXPORT_NOT_FOUND", "Export not found.")
    return e


def voice_snapshot(svc: Services, c: Clip, edit: dict[str, Any]) -> dict[str, Any]:
    """Placed takes with their files and measurements, the merged ducking segments and voice coverage."""
    from clipper.api.voice import voice_key

    dur = (c.end_ms - c.start_ms) / 1000
    ids = [v["track_id"] for v in edit["voiceovers"]]
    with svc.db.session() as s:
        tracks = {t.id: t for t in s.scalars(select(VoiceTrack).where(VoiceTrack.id.in_(ids)))} if ids else {}
        takes = [
            {"track_id": t.id, "key": voice_key(t), "name": t.name, "start": v["start"],
             "duration": t.duration_ms / 1000, "loudness_lufs": t.loudness_lufs, "segments": t.segments}
            for v in edit["voiceovers"]
            if (t := tracks.get(v["track_id"])) is not None and v["start"] < dur
        ]  # fmt: skip
    segs = vo.placed_segments(((tk["start"], tk["segments"]) for tk in takes), dur)
    return {"audio": edit["audio"], "takes": takes, "segments": segs, "coverage": vo.coverage(segs, dur)}


def settings_for(svc: Services, c: Clip, quality: str) -> dict[str, Any]:
    """Everything the renderer needs, frozen now: later edits never change a queued export."""
    a = _get(svc, c.asset_id)
    edit = normalised(c.edit)
    groups: list[dict[str, Any]] = []
    transcript = None
    try:
        meta, data = _read_transcript(svc, a)
        transcript = {
            "model": meta["model"],
            "language": meta["language"],
            "version": meta["version"],
            "edited": meta["edited"],
        }
        # grouped even when burned-in captions are off: the SRT/VTT sidecars still use them
        preset = PRESETS[edit["captions"]["preset"]]
        groups = group_words(data.get("words", []), c.start_ms / 1000, c.end_ms / 1000, preset)
    except ApiError:
        pass
    st: dict[str, Any] = {
        "clip_id": c.id,
        "clip_version": c.version,
        "start_ms": c.start_ms,
        "end_ms": c.end_ms,
        "title": c.title,
        "edit": edit,
        "quality": quality,
        "groups": groups,
        "overlays": overlay_layout(edit["overlays"], (c.end_ms - c.start_ms) / 1000),
        "voice": voice_snapshot(svc, c, edit),
        "transcript": transcript,
        "renderer": 2,  # bump when output for the same settings changes
    }
    st["hash"] = hashlib.sha256(json.dumps(st, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return st


@router.get("/export-qualities")
def export_qualities() -> dict[str, Any]:
    return {"items": [q.to_json() for q in QUALITIES.values()], "default": "standard"}


@router.post("/clips/{clip_id}/exports", status_code=202, response_model=ExportOut)
def create_export(
    clip_id: str,
    body: ExportCreate,
    svc: Svc,
    if_match: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    c = _load(svc, clip_id)
    a = _get(svc, c.asset_id)
    if a.status != AssetStatus.READY:
        raise ApiError(409, "ASSET_NOT_READY", "This video is still being prepared.")
    if if_match is not None and _expected_version(if_match, c.version, "clip") != c.version:
        raise ApiError(
            412,
            "VERSION_CONFLICT",
            "This clip was changed elsewhere.",
            "Reload it, check the changes, then export again.",
            version=c.version,
        )
    _bounds(a, c.start_ms, c.end_ms)
    st = settings_for(svc, c, body.quality)
    seconds = (c.end_ms - c.start_ms) / 1000
    need = int(seconds * 2_000_000) * 2 + EXPORT_MARGIN  # ~16 Mbit/s worst case, x2 for the work copy
    if svc.storage.free_bytes() < need:
        raise ApiError(
            507,
            "DISK_FULL",
            "Not enough free disk space to export this clip.",
            f"Free at least {need / 1024**3:.1f} GB on the drive holding CLIPPER_DATA_DIR.",
        )
    with svc.db.session() as s:
        # a double click (or two tabs) must not render the same thing twice
        dup = s.scalars(
            select(Export).where(
                Export.clip_id == c.id,
                Export.settings_hash == st["hash"],
                Export.status.in_([ExportStatus.QUEUED, ExportStatus.RENDERING]),
            )
        ).first()
        if dup is not None:
            return JSONResponse(out(svc, dup).model_dump(mode="json"), status_code=200)
        e = Export(
            project_id=c.project_id,
            asset_id=c.asset_id,
            clip_id=c.id,
            quality=body.quality,
            clip_version=c.version,
            settings=st,
            settings_hash=st["hash"],
            title=c.title,
        )
        s.add(e)
        s.flush()
        export_id = e.id
    job_id = svc.queue.enqueue("render.export", {"export_id": export_id}, project_id=c.project_id)
    with svc.db.session() as s:
        row = s.get(Export, export_id)
        if row is None:  # deleted in the meantime
            raise ApiError(404, "EXPORT_NOT_FOUND", "Export not found.")
        row.job_id = job_id
        s.flush()
        e = row
    return JSONResponse(out(svc, e).model_dump(mode="json"), status_code=202)


@router.get("/clips/{clip_id}/exports", response_model=Page[ExportOut])
def list_clip_exports(clip_id: str, svc: Svc) -> dict[str, Any]:
    c = _load(svc, clip_id)
    with svc.db.session() as s:
        rows = list(
            s.scalars(select(Export).where(Export.clip_id == c.id).order_by(Export.created_at.desc()))
        )
    return {"items": [out(svc, e) for e in rows], "next_cursor": None}


@router.get("/projects/{project_id}/exports", response_model=Page[ExportOut])
def list_project_exports(
    project_id: str, svc: Svc, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> dict[str, Any]:
    with svc.db.session() as s:
        rows = list(
            s.scalars(
                select(Export)
                .where(Export.project_id == _id(project_id))
                .order_by(Export.created_at.desc())
                .limit(limit)
            )
        )
    return {"items": [out(svc, e) for e in rows], "next_cursor": None}


@router.get("/exports/{export_id}", response_model=ExportOut)
def get_export(export_id: str, svc: Svc) -> ExportOut:
    return out(svc, _export(svc, export_id))


def download_name(title: str | None, export_id: str, kind: str) -> str:
    """ASCII-safe file name from the clip title, e.g. 'my-best-moment-01a1e5.mp4'."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "clip").lower()).strip("-")[:60] or "clip"
    return f"{slug}-{export_id[-6:]}.{kind}"


@router.get("/exports/{export_id}/files/{kind}")
def export_file(
    export_id: str, kind: str, svc: Svc, download: Annotated[bool, Query()] = False
) -> FileResponse:
    if kind not in KINDS:
        raise ApiError(404, "NOT_FOUND", "Unknown file type.", f"Use one of {sorted(KINDS)}.")
    e = _export(svc, export_id)
    if e.status != ExportStatus.DONE or kind not in (e.files or {}):
        raise ApiError(404, "EXPORT_FILE_NOT_FOUND", "This file is not available for this export.")
    path = svc.storage.path(f"{export_prefix(e.project_id, e.asset_id, e.id)}/clip.{kind}")
    if not path.exists():
        raise ApiError(410, "EXPORT_FILE_GONE", "The exported file is missing from disk.", "Export again.")
    return FileResponse(
        path,
        media_type=KINDS[kind],
        filename=download_name(e.title, e.id, kind) if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


def stop_exports(svc: Services, *, clip_id: str | None = None, asset_id: str | None = None) -> bool:
    """Request cancellation of unfinished exports; True if one is still running (caller should retry)."""
    running = False
    with svc.db.session() as s:
        q = select(Export.job_id).where(Export.status.in_([ExportStatus.QUEUED, ExportStatus.RENDERING]))
        q = q.where(Export.clip_id == clip_id) if clip_id else q.where(Export.asset_id == asset_id)
        job_ids = [j for j in s.scalars(q) if j]
        for jid in job_ids:
            job = s.get(Job, jid)
            running = running or (job is not None and job.status == JobStatus.RUNNING)
    for jid in job_ids:
        svc.queue.request_cancel(jid)
    return running


@router.delete("/exports/{export_id}", status_code=204)
def delete_export(export_id: str, svc: Svc) -> Response:
    e = _export(svc, export_id)
    if e.job_id and e.status in (ExportStatus.QUEUED, ExportStatus.RENDERING):
        with svc.db.session() as s:
            job = s.get(Job, e.job_id)
            running = job is not None and job.status == JobStatus.RUNNING
        svc.queue.request_cancel(e.job_id)
        if running:
            raise ApiError(
                409,
                "EXPORT_RUNNING",
                "This export is still rendering.",
                "It is being cancelled. Try deleting again in a few seconds.",
            )
    try:
        svc.storage.delete_prefix(export_prefix(e.project_id, e.asset_id, e.id))
    except OSError as err:
        raise ApiError(
            409,
            "FILES_IN_USE",
            "Some files could not be deleted.",
            "Close any program that has the exported video open, then try again.",
        ) from err
    with svc.db.session() as s:
        row = s.get(Export, e.id)
        if row is not None:
            s.delete(row)
    return Response(status_code=204)


@router.get("/clips/{clip_id}/captions.{fmt}")
def clip_caption_file(clip_id: str, fmt: str, svc: Svc) -> Response:
    if fmt not in ("srt", "vtt"):
        raise ApiError(404, "NOT_FOUND", "Use captions.srt or captions.vtt.")
    c = _load(svc, clip_id)
    a = _get(svc, c.asset_id)
    _, data = _read_transcript(svc, a)
    preset = PRESETS[normalised(c.edit)["captions"]["preset"]]
    groups = group_words(data.get("words", []), c.start_ms / 1000, c.end_ms / 1000, preset)
    body = subs.srt(groups) if fmt == "srt" else subs.vtt(groups)
    return Response(body, media_type=KINDS[fmt] + "; charset=utf-8")
