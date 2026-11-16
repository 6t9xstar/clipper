"""Voiceover takes for a clip.

POST   /clips/{id}/voice?name=Take%201   raw audio body (browser recording or an audio file) -> 201 VoiceTrack
GET    /clips/{id}/voice                 takes of this clip
GET    /voice/{id}/audio                 the converted take (FLAC), for the editor's preview player
DELETE /voice/{id}                       409 VOICE_IN_USE while the saved clip or a running export uses it

Placing a take in the clip and its levels are part of the clip edit (`edit.voiceovers`, `edit.audio`).
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

import anyio
from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from clipper.api.assets import Svc
from clipper.api.clips import _load
from clipper.api.errors import ApiError
from clipper.core.ids import new_id
from clipper.core.services import Services
from clipper.db.models import Clip, Export, ExportStatus, VoiceTrack
from clipper.edit.schema import normalised
from clipper.media import voice as mv
from clipper.media.importer import asset_prefix

router = APIRouter(prefix="/api/v1", tags=["voiceover"])
MAX_TAKES = 30
ACCEPTED = ("audio/", "video/webm", "video/mp4", "video/ogg", "application/octet-stream")
_busy: dict[str, asyncio.Lock] = {}


def voice_prefix(project_id: str, asset_id: str, track_id: str) -> str:
    return f"{asset_prefix(project_id, asset_id)}/voice/{track_id}"


def voice_key(t: VoiceTrack) -> str:
    return f"{voice_prefix(t.project_id, t.asset_id, t.id)}/voice.flac"


def out(t: VoiceTrack) -> dict[str, Any]:
    return {
        "id": t.id,
        "clip_id": t.clip_id,
        "name": t.name,
        "duration_ms": t.duration_ms,
        "loudness_lufs": t.loudness_lufs,
        "segments": t.segments,
        "peaks": t.peaks,
        "created_at": t.created_at,
    }


def _track(svc: Services, track_id: str) -> VoiceTrack:
    with svc.db.session() as s:
        t = s.get(VoiceTrack, track_id)
        if t is None:
            raise ApiError(404, "VOICE_NOT_FOUND", "Voice recording not found.")
        s.expunge(t)
        return t


@router.post("/clips/{clip_id}/voice", status_code=201)
async def upload_voice(
    clip_id: str, request: Request, svc: Svc, name: str = Query(default="", max_length=100)
) -> dict[str, Any]:
    ctype = request.headers.get("content-type", "application/octet-stream").lower()
    if not ctype.startswith(ACCEPTED):
        raise ApiError(415, "VOICE_TYPE", "Send an audio recording or audio file.")
    c = await anyio.to_thread.run_sync(_load, svc, clip_id)
    with svc.db.session() as s:
        n = len(list(s.scalars(select(VoiceTrack.id).where(VoiceTrack.clip_id == c.id))))
    if n >= MAX_TAKES:
        raise ApiError(409, "VOICE_LIMIT", f"A clip can have at most {MAX_TAKES} recordings.",
                       "Delete takes you no longer need.")  # fmt: skip
    if svc.storage.free_bytes() < 2 * mv.MAX_BYTES:
        raise ApiError(507, "DISK_FULL", "Not enough free disk space to store the recording.")
    lock = _busy.setdefault(c.id, asyncio.Lock())
    async with lock:  # one conversion per clip at a time
        track = VoiceTrack(id=new_id(), project_id=c.project_id, asset_id=c.asset_id, clip_id=c.id,
                           name=name.strip() or f"Take {n + 1}", duration_ms=0)  # fmt: skip
        prefix = voice_prefix(c.project_id, c.asset_id, track.id)
        folder = svc.storage.path(prefix)
        folder.mkdir(parents=True, exist_ok=True)
        upload = folder / "upload.bin"
        try:
            size = 0
            fh = await anyio.to_thread.run_sync(upload.open, "wb")
            try:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > mv.MAX_BYTES:
                        raise ApiError(413, "VOICE_TOO_LARGE", "Recordings can be at most 200 MB.")
                    await anyio.to_thread.run_sync(fh.write, chunk)
            finally:
                await anyio.to_thread.run_sync(fh.close)
            if size == 0:
                raise ApiError(400, "VOICE_EMPTY", "The recording is empty.")
            try:
                info = await anyio.to_thread.run_sync(
                    lambda: mv.prepare_track(upload, folder / "voice.flac", lambda: False)
                )
            except mv.VoiceError as err:
                raise ApiError(422, err.code, str(err)) from err
            upload.unlink(missing_ok=True)
            track.duration_ms = info["duration_ms"]
            track.loudness_lufs = info["loudness_lufs"]
            track.segments = info["segments"]
            track.peaks = info["peaks"]
            with svc.db.session() as s:
                if s.get(Clip, c.id) is None:  # deleted while converting
                    raise ApiError(404, "CLIP_NOT_FOUND", "Clip not found.")
                s.add(track)
                s.flush()
                result = out(track)
        except BaseException:
            await anyio.to_thread.run_sync(lambda: shutil.rmtree(folder, ignore_errors=True))
            raise
    _busy.pop(c.id, None)
    return result


@router.get("/clips/{clip_id}/voice")
def list_voice(clip_id: str, svc: Svc) -> dict[str, Any]:
    c = _load(svc, clip_id)
    with svc.db.session() as s:
        rows = s.scalars(select(VoiceTrack).where(VoiceTrack.clip_id == c.id).order_by(VoiceTrack.created_at))
        return {"items": [out(t) for t in rows]}


@router.get("/voice/{track_id}/audio")
def voice_audio(track_id: str, svc: Svc) -> FileResponse:
    t = _track(svc, track_id)
    path = svc.storage.path(voice_key(t))
    if not path.exists():
        raise ApiError(410, "VOICE_FILE_GONE", "The recording file is missing from disk.")
    return FileResponse(path, media_type="audio/flac")


def in_use(svc: Services, t: VoiceTrack) -> bool:
    with svc.db.session() as s:
        c = s.get(Clip, t.clip_id)
        if c is not None and any(v["track_id"] == t.id for v in normalised(c.edit)["voiceovers"]):
            return True
        active = s.scalars(
            select(Export.settings).where(
                Export.clip_id == t.clip_id,
                Export.status.in_([ExportStatus.QUEUED, ExportStatus.RENDERING]),
            )
        )
        return any(
            any(v.get("track_id") == t.id for v in (st.get("voice") or {}).get("takes", [])) for st in active
        )


@router.delete("/voice/{track_id}", status_code=204)
def delete_voice(track_id: str, svc: Svc) -> Response:
    t = _track(svc, track_id)
    if in_use(svc, t):
        raise ApiError(409, "VOICE_IN_USE", "This recording is used in the saved clip or a running export.",
                       "Remove it from the clip and save first.")  # fmt: skip
    svc.storage.delete_prefix(voice_prefix(t.project_id, t.asset_id, t.id))
    with svc.db.session() as s:
        row = s.get(VoiceTrack, t.id)
        if row is not None:
            s.delete(row)
    return Response(status_code=204)


def delete_clip_voice(svc: Services, c: Clip) -> None:
    with svc.db.session() as s:
        ids = list(s.scalars(select(VoiceTrack.id).where(VoiceTrack.clip_id == c.id)))
    for tid in ids:  # rows go with the clip (FK cascade); files must go too
        svc.storage.delete_prefix(voice_prefix(c.project_id, c.asset_id, tid))
