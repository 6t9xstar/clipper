"""Asset import API: create -> resumable chunked upload -> automatic import job -> media endpoints.

Upload protocol (tus-like, deliberately small):
  POST /projects/{pid}/assets           declare name, exact size, rights -> asset + upload_url
  GET  /assets/{id}/upload              -> {"offset": n}  (resume point)
  PUT  /assets/{id}/upload              header Upload-Offset: n, body = next chunk (<= 64 MiB)
                                        409 OFFSET_MISMATCH carries the server offset; when the last byte
                                        arrives the import job is queued automatically.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from clipper.api.errors import ApiError
from clipper.api.schemas import AssetCreate, AssetOut, Page
from clipper.core.ids import is_valid_id
from clipper.core.services import Services
from clipper.db.models import TERMINAL, Asset, AssetStatus, Job, JobStatus, Project
from clipper.media.errors import MESSAGES
from clipper.media.importer import asset_prefix

MAX_CHUNK = 64 * 1024 * 1024
DISK_MARGIN = 1024**3

router = APIRouter(prefix="/api/v1")
_locks: dict[str, asyncio.Lock] = {}


def _svc(request: Request) -> Services:
    s: Services = request.app.state.clipper
    return s


Svc = Annotated[Services, Depends(_svc)]


def _id(v: str) -> str:
    if not is_valid_id(v):
        raise ApiError(404, "NOT_FOUND", "Not found.")
    return v


def reconcile(svc: Services, a: Asset) -> Asset:
    """If the import job ended without updating the asset (crash on last attempt, cancel), reflect that."""
    if a.status != AssetStatus.PROCESSING or not a.import_job_id:
        return a
    with svc.db.session() as s:
        job = s.get(Job, a.import_job_id)
        if job is not None and job.status in TERMINAL and job.status != JobStatus.SUCCEEDED:
            row = s.get(Asset, a.id)
            if row is not None and row.status == AssetStatus.PROCESSING:
                row.status = AssetStatus.FAILED
                row.error_code = (
                    "IMPORT_CANCELLED"
                    if job.status == JobStatus.CANCELLED
                    else (job.error_code or "MEDIA_PROCESSING_FAILED")
                )
                row.error_message = (
                    "Import was cancelled."
                    if job.status == JobStatus.CANCELLED
                    else (MESSAGES.get(row.error_code, MESSAGES["MEDIA_PROCESSING_FAILED"])[0])
                )
                s.flush()
                return row
    return a


def out(a: Asset) -> AssetOut:
    o = AssetOut.model_validate(a)
    if a.error_code in MESSAGES:
        o.error_hint = MESSAGES[a.error_code][1]
    elif a.error_code == "IMPORT_CANCELLED":
        o.error_hint = "Delete this item and import the file again."
    if a.status == AssetStatus.UPLOADING:
        o.upload_url = f"/api/v1/assets/{a.id}/upload"
    return o


def _get(svc: Services, asset_id: str) -> Asset:
    with svc.db.session() as s:
        a = s.get(Asset, _id(asset_id))
    if a is None:
        raise ApiError(404, "ASSET_NOT_FOUND", "Video not found.")
    return reconcile(svc, a)


@router.post("/projects/{project_id}/assets", status_code=201, response_model=AssetOut)
def create_asset(project_id: str, body: AssetCreate, svc: Svc) -> AssetOut:
    st = svc.settings
    if body.size_bytes > st.max_upload_bytes:
        raise ApiError(
            413,
            "UPLOAD_TOO_LARGE",
            "This file is larger than the allowed maximum.",
            f"Maximum is {st.max_upload_bytes / 1024**3:.1f} GB (CLIPPER_MAX_UPLOAD_BYTES).",
        )
    need = int(body.size_bytes * 1.3) + DISK_MARGIN  # source + proxy + audio + headroom
    if svc.storage.free_bytes() < need:
        raise ApiError(
            507,
            "DISK_FULL",
            "Not enough free disk space for this video.",
            f"Free at least {need / 1024**3:.1f} GB on the drive holding CLIPPER_DATA_DIR.",
        )
    with svc.db.session() as s:
        if s.get(Project, _id(project_id)) is None:
            raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
        a = Asset(project_id=project_id, display_name=body.filename, size_bytes=body.size_bytes,
                  rights_type=body.rights_type, rights_note=(body.rights_note or "").strip() or None,
                  rights_confirmed_at=time.time(), status=AssetStatus.UPLOADING, warnings=[])  # fmt: skip
        s.add(a)
    svc.storage.path(asset_prefix(project_id, a.id)).mkdir(parents=True, exist_ok=True)
    return out(a)


@router.get("/projects/{project_id}/assets", response_model=Page[AssetOut])
def list_assets(project_id: str, svc: Svc) -> dict[str, Any]:
    with svc.db.session() as s:
        rows = list(s.scalars(select(Asset).where(Asset.project_id == _id(project_id)).order_by(Asset.id)))
    return {"items": [out(reconcile(svc, a)) for a in rows], "next_cursor": None}


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: str, svc: Svc) -> AssetOut:
    return out(_get(svc, asset_id))


@router.get("/assets/{asset_id}/upload")
def upload_status(asset_id: str, svc: Svc) -> dict[str, Any]:
    a = _get(svc, asset_id)
    return {"offset": a.received_bytes, "size": a.size_bytes, "status": a.status}


@router.put("/assets/{asset_id}/upload", response_model=AssetOut)
async def upload_chunk(
    asset_id: str, request: Request, svc: Svc, upload_offset: Annotated[int, Header(ge=0)]
) -> AssetOut:
    a = await anyio.to_thread.run_sync(_get, svc, asset_id)
    lock = _locks.setdefault(a.id, asyncio.Lock())
    if lock.locked():
        raise ApiError(409, "UPLOAD_IN_PROGRESS", "Another upload to this video is already running.")
    async with lock:
        a = await anyio.to_thread.run_sync(_get, svc, asset_id)  # re-read under the lock
        if a.status != AssetStatus.UPLOADING:
            raise ApiError(409, "UPLOAD_CLOSED", "This video is no longer accepting data.")
        if upload_offset != a.received_bytes:
            raise ApiError(
                409,
                "OFFSET_MISMATCH",
                "Upload position mismatch; resume from the server offset.",
                offset=a.received_bytes,
            )
        path = svc.storage.path(f"{asset_prefix(a.project_id, a.id)}/upload.part")
        written = await _write_chunk(path, upload_offset, a.size_bytes, request)
        received = upload_offset + written
        job_id = None
        with svc.db.session() as s:
            row = s.get(Asset, a.id)
            if row is None:
                raise ApiError(404, "ASSET_NOT_FOUND", "Video not found.")
            row.received_bytes = received
            if received == row.size_bytes:
                row.status = AssetStatus.PROCESSING
                job_id = svc.queue.enqueue("media.import", {"asset_id": a.id}, project_id=a.project_id)
                row.import_job_id = job_id
            s.flush()
            result = out(row)
    _locks.pop(a.id, None)
    return result


async def _write_chunk(path: Path, offset: int, size: int, request: Request) -> int:
    def open_at() -> Any:
        fh = path.open("r+b" if path.exists() else "w+b")
        fh.truncate(offset)  # drop bytes written after the last acknowledged offset (crash mid-chunk)
        fh.seek(offset)
        return fh

    fh = await anyio.to_thread.run_sync(open_at)
    written = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            written += len(chunk)
            if written > MAX_CHUNK:
                raise ApiError(
                    413, "CHUNK_TOO_LARGE", f"Send at most {MAX_CHUNK // 1024**2} MiB per request."
                )
            if offset + written > size:
                raise ApiError(400, "UPLOAD_EXCEEDS_SIZE", "More data was sent than the declared file size.")
            await anyio.to_thread.run_sync(fh.write, chunk)
        await anyio.to_thread.run_sync(fh.flush)
    except BaseException:
        await anyio.to_thread.run_sync(fh.truncate, offset)  # reject the whole chunk
        raise
    finally:
        await anyio.to_thread.run_sync(fh.close)
    return written


def _ready_file(svc: Services, asset_id: str, name: str) -> Path:
    a = _get(svc, asset_id)
    if a.status != AssetStatus.READY:
        raise ApiError(409, "ASSET_NOT_READY", "This video is still being prepared.")
    p = svc.storage.path(f"{asset_prefix(a.project_id, a.id)}/derived/{name}")
    if not p.exists():
        raise ApiError(404, "ARTIFACT_MISSING", "This file is not available.")
    return p


@router.get("/assets/{asset_id}/proxy")
def get_proxy(asset_id: str, svc: Svc) -> FileResponse:
    """Low-resolution preview for the editor. Supports HTTP Range (seeking)."""
    return FileResponse(_ready_file(svc, asset_id, "proxy.mp4"), media_type="video/mp4")


@router.get("/assets/{asset_id}/thumbnails")
def get_thumbnails(asset_id: str, svc: Svc) -> FileResponse:
    """Sprite sheet; tile layout is in asset.media_info.thumbs."""
    return FileResponse(_ready_file(svc, asset_id, "thumbs.jpg"), media_type="image/jpeg")


@router.get("/assets/{asset_id}/waveform")
def get_waveform(asset_id: str, svc: Svc) -> Response:
    a = _get(svc, asset_id)
    if a.status == AssetStatus.READY and a.has_audio is False:
        return Response(json.dumps({"rate": 0, "peaks": []}), media_type="application/json")
    return FileResponse(_ready_file(svc, asset_id, "waveform.json"), media_type="application/json")


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: str, svc: Svc) -> Response:
    a = _get(svc, asset_id)
    if _locks.get(a.id) and _locks[a.id].locked():
        raise ApiError(409, "UPLOAD_IN_PROGRESS", "An upload is running. Stop it, then delete.")
    for job_id in filter(None, (a.import_job_id, a.analysis_job_id)):
        with svc.db.session() as s:
            job = s.get(Job, job_id)
            running = job is not None and job.status == JobStatus.RUNNING
        svc.queue.request_cancel(job_id)  # queued -> cancelled at once; running -> flag the worker acts on
        if running:
            raise ApiError(
                409,
                "ASSET_BUSY",
                "This video is being processed.",
                "It is being cancelled. Try deleting again in a few seconds.",
            )
    try:
        svc.storage.delete_prefix(asset_prefix(a.project_id, a.id))
    except OSError as e:
        raise ApiError(
            409, "FILES_IN_USE", "Some files are still in use.", "Try again in a few seconds."
        ) from e
    with svc.db.session() as s:
        row = s.get(Asset, a.id)
        if row is not None:
            s.delete(row)
    return Response(status_code=204)
