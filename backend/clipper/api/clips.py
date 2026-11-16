"""Analysis, transcript and clip endpoints.

POST   /assets/{id}/analyze      queue `ai.analyze` (202 + job). One analysis per video at a time.
GET    /assets/{id}/transcript   words with timings (404 until transcribed); ETag: "<version>"
PATCH  /assets/{id}/transcript   correct words (If-Match required)
GET    /assets/{id}/clips        suggestions + manual clips, best first (?status= filter)
POST   /assets/{id}/clips        manual clip
GET    /clips/{id}               ETag: "<version>"
PATCH  /clips/{id}               requires If-Match: "<version>" (412 VERSION_CONFLICT if stale)
DELETE /clips/{id}
GET    /clips/{id}/captions      caption groups for the clip (same data the renderer uses)
GET    /caption-presets
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import case, select, update

from clipper.api.assets import Svc, _get, _id
from clipper.api.errors import ApiError
from clipper.api.schemas import (
    AnalyzeRequest,
    ClipCreate,
    ClipOut,
    ClipUpdate,
    JobOut,
    Page,
    TranscriptEdit,
)
from clipper.core.services import Services
from clipper.db.models import TERMINAL, Asset, AssetStatus, Clip, ClipStatus, Job, Transcript
from clipper.edit.captions import PRESETS, group_words
from clipper.edit.schema import normalised
from clipper.media.importer import asset_prefix

if TYPE_CHECKING:
    from sqlalchemy import CursorResult

MIN_CLIP_MS, MAX_CLIP_MS = 1_000, 600_000

router = APIRouter(prefix="/api/v1")


def _ready(svc: Services, asset_id: str) -> Asset:
    a = _get(svc, asset_id)
    if a.status != AssetStatus.READY:
        raise ApiError(409, "ASSET_NOT_READY", "This video is still being prepared.")
    return a


def _bounds(a: Asset, start_ms: int, end_ms: int) -> None:
    dur = a.duration_ms or 0
    if not (0 <= start_ms < end_ms <= dur):
        raise ApiError(
            422,
            "CLIP_OUT_OF_RANGE",
            "The clip must lie inside the video.",
            f"Use 0 ≤ start < end ≤ {dur} ms.",
        )
    if not (MIN_CLIP_MS <= end_ms - start_ms <= MAX_CLIP_MS):
        raise ApiError(422, "CLIP_LENGTH", "Clips must be between 1 second and 10 minutes long.")


def _etag(c: Clip) -> str:
    return f'"{c.version}"'


def _expected_version(if_match: str | None, current: int, what: str) -> int:
    if not if_match:
        raise ApiError(428, "PRECONDITION_REQUIRED", f"Send the {what}'s ETag in an If-Match header.")
    tag = if_match.strip()
    if tag == "*":
        return current
    try:
        return int(tag.removeprefix("W/").strip('"'))
    except ValueError:
        raise ApiError(400, "BAD_IF_MATCH", 'If-Match must look like "3".') from None


def _transcript_path(svc: Services, a: Asset) -> Path:
    return svc.storage.path(f"{asset_prefix(a.project_id, a.id)}/derived/transcript.json")


def _clip_response(c: Clip, status: int = 200) -> JSONResponse:
    return JSONResponse(
        ClipOut.model_validate(c).model_dump(mode="json"), status_code=status, headers={"ETag": _etag(c)}
    )


@router.post("/assets/{asset_id}/analyze", status_code=202, response_model=JobOut)
def analyze(asset_id: str, body: AnalyzeRequest, svc: Svc) -> Job:
    a = _ready(svc, asset_id)
    with svc.db.session() as s:
        t = s.get(Transcript, a.id)
        if body.retranscribe and t is not None and t.edited and not body.discard_edits:
            raise ApiError(
                409,
                "TRANSCRIPT_EDITED",
                "Transcribing again would overwrite your transcript corrections.",
                'Send "discard_edits": true to do it anyway.',
            )
        if a.analysis_job_id:
            prev = s.get(Job, a.analysis_job_id)
            if prev is not None and prev.status not in TERMINAL:
                raise ApiError(
                    409,
                    "ANALYSIS_RUNNING",
                    "This video is already being analysed.",
                    "Wait for it to finish or cancel it first.",
                    job_id=prev.id,
                )
        payload = body.model_dump(exclude_none=True, exclude={"discard_edits"})
        job_id = svc.queue.enqueue("ai.analyze", {"asset_id": a.id, **payload}, project_id=a.project_id)
        row = s.get(Asset, a.id)
        if row is not None:
            row.analysis_job_id = job_id
    with svc.db.session() as s:
        job = s.get(Job, job_id)
        if job is None:  # pragma: no cover - deleted between the two statements
            raise ApiError(404, "JOB_NOT_FOUND", "Job not found.")
        return job


def _read_transcript(svc: Services, a: Asset) -> tuple[dict[str, Any], dict[str, Any]]:
    with svc.db.session() as s:
        t = s.get(Transcript, a.id)
        meta = (
            None
            if t is None
            else {
                "language": t.language,
                "language_probability": t.language_probability,
                "model": t.model,
                "device": t.device,
                "word_count": t.word_count,
                "seconds_taken": t.seconds_taken,
                "edited": t.edited,
                "version": t.version,
            }
        )
    p = _transcript_path(svc, a)
    if meta is None or not p.exists():
        raise ApiError(
            404, "TRANSCRIPT_NOT_FOUND", "This video has not been transcribed yet.", "Run Analyze first."
        )
    data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return meta, data


def _transcript_response(svc: Services, a: Asset) -> JSONResponse:
    meta, data = _read_transcript(svc, a)
    body = {**meta, "notes": data.get("notes", []), "words": data.get("words", [])}
    return JSONResponse(body, headers={"ETag": f'"{meta["version"]}"'})


@router.get("/assets/{asset_id}/transcript")
def get_transcript(asset_id: str, svc: Svc) -> JSONResponse:
    return _transcript_response(svc, _get(svc, asset_id))


@router.patch("/assets/{asset_id}/transcript")
def edit_transcript(
    asset_id: str, body: TranscriptEdit, svc: Svc, if_match: Annotated[str | None, Header()] = None
) -> JSONResponse:
    """Correct words (text only; timings are kept). An empty text hides the word."""
    a = _get(svc, asset_id)
    meta, data = _read_transcript(svc, a)
    expected = _expected_version(if_match, meta["version"], "transcript")
    n_words = len(data.get("words", []))
    for e in body.words:
        if e.i >= n_words:
            raise ApiError(422, "WORD_INDEX", f"Word {e.i} does not exist (transcript has {n_words} words).")
    with svc.db.session() as s:
        res = s.execute(
            update(Transcript)
            .where(Transcript.asset_id == a.id, Transcript.version == expected)
            .values(version=expected + 1, edited=True)
            .execution_options(synchronize_session=False)
        )
        if cast("CursorResult[Any]", res).rowcount != 1:
            raise ApiError(
                412,
                "VERSION_CONFLICT",
                "The transcript was changed elsewhere.",
                "Reload it and apply your change again.",
                version=meta["version"],
            )
        # Holding the version row: re-read, apply, replace the file atomically. The DB change commits only
        # after the file is written, so a failed write leaves the old version in place.
        data = json.loads(_transcript_path(svc, a).read_text(encoding="utf-8"))
        for e in body.words:
            data["words"][e.i]["w"] = e.w
        with svc.storage.open_write(f"{asset_prefix(a.project_id, a.id)}/derived/transcript.json") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return _transcript_response(svc, a)


@router.get("/assets/{asset_id}/clips", response_model=Page[ClipOut])
def list_clips(
    asset_id: str,
    svc: Svc,
    status: Annotated[str | None, Query(pattern=r"^(suggested|approved|rejected)$")] = None,
) -> dict[str, Any]:
    a = _get(svc, asset_id)
    q = select(Clip).where(Clip.asset_id == a.id)
    if status:
        q = q.where(Clip.status == status)
    # ranked suggestions first (best first), then manual clips by position
    q = q.order_by(case((Clip.rank.is_(None), 1), else_=0), Clip.rank, Clip.start_ms, Clip.id)
    with svc.db.session() as s:
        rows = list(s.scalars(q))
    return {"items": [ClipOut.model_validate(c) for c in rows], "next_cursor": None}


@router.post("/assets/{asset_id}/clips", status_code=201, response_model=ClipOut)
def create_clip(asset_id: str, body: ClipCreate, svc: Svc) -> JSONResponse:
    a = _ready(svc, asset_id)
    _bounds(a, body.start_ms, body.end_ms)
    with svc.db.session() as s:
        c = Clip(
            project_id=a.project_id,
            asset_id=a.id,
            start_ms=body.start_ms,
            end_ms=body.end_ms,
            origin="manual",
            status=ClipStatus.APPROVED,
            title=body.title,
            reasons=[],
            features={},
            edit={},
        )
        s.add(c)
        s.flush()
        return _clip_response(c, 201)


def _load(svc: Services, clip_id: str) -> Clip:
    with svc.db.session() as s:
        c = s.get(Clip, _id(clip_id))
    if c is None:
        raise ApiError(404, "CLIP_NOT_FOUND", "Clip not found.")
    return c


@router.get("/clips/{clip_id}", response_model=ClipOut)
def get_clip(clip_id: str, svc: Svc) -> JSONResponse:
    return _clip_response(_load(svc, clip_id))


@router.patch("/clips/{clip_id}", response_model=ClipOut)
def update_clip(
    clip_id: str, body: ClipUpdate, svc: Svc, if_match: Annotated[str | None, Header()] = None
) -> JSONResponse:
    c = _load(svc, clip_id)
    expected = _expected_version(if_match, c.version, "clip")
    a = _get(svc, c.asset_id)
    changes = body.model_dump(exclude_unset=True)
    start, end = changes.get("start_ms", c.start_ms), changes.get("end_ms", c.end_ms)
    if "start_ms" in changes or "end_ms" in changes:
        _bounds(a, start, end)
    with svc.db.session() as s:
        # compare-and-swap in one statement: atomic on SQLite and PostgreSQL alike
        res = s.execute(
            update(Clip)
            .where(Clip.id == c.id, Clip.version == expected)
            .values(**changes, version=expected + 1, updated_at=time.time())
            .execution_options(synchronize_session=False)
        )
        n = cast("CursorResult[Any]", res).rowcount
        row = s.get(Clip, c.id, populate_existing=True)
        if row is None:
            raise ApiError(404, "CLIP_NOT_FOUND", "Clip not found.")
        if n != 1:
            raise ApiError(
                412,
                "VERSION_CONFLICT",
                "This clip was changed elsewhere.",
                "Reload it and apply your change again.",
                version=row.version,
            )
        return _clip_response(row)


@router.delete("/clips/{clip_id}", status_code=204)
def delete_clip(clip_id: str, svc: Svc) -> Response:
    from clipper.api.exports import stop_exports
    from clipper.db.models import Export
    from clipper.render.export import export_prefix

    c = _load(svc, clip_id)
    if stop_exports(svc, clip_id=c.id):
        raise ApiError(409, "CLIP_BUSY", "This clip is being exported.",
                       "The export is being cancelled. Try deleting again in a few seconds.")  # fmt: skip
    with svc.db.session() as s:
        export_ids = list(s.scalars(select(Export.id).where(Export.clip_id == c.id)))
    for eid in export_ids:  # the rows go with the clip (FK cascade); the files must go too
        svc.storage.delete_prefix(export_prefix(c.project_id, c.asset_id, eid))
    with svc.db.session() as s:
        row = s.get(Clip, c.id)
        if row is not None:
            s.delete(row)
    return Response(status_code=204)


@router.get("/caption-presets")
def caption_presets() -> dict[str, Any]:
    return {"items": [p.to_json() for p in PRESETS.values()], "frame": {"width": 1080, "height": 1920}}


@router.get("/clips/{clip_id}/captions")
def clip_captions(
    clip_id: str,
    svc: Svc,
    preset: Annotated[str | None, Query(max_length=32)] = None,
    start_ms: Annotated[int | None, Query(ge=0)] = None,
    end_ms: Annotated[int | None, Query(gt=0)] = None,
) -> dict[str, Any]:
    """Caption groups for the clip, times relative to its start.

    `preset`, `start_ms` and `end_ms` preview unsaved changes (another preset, a trim in progress)."""
    c = _load(svc, clip_id)
    a = _get(svc, c.asset_id)
    lo, hi = start_ms if start_ms is not None else c.start_ms, end_ms if end_ms is not None else c.end_ms
    _bounds(a, lo, hi)
    pid = preset or normalised(c.edit)["captions"]["preset"]
    if pid not in PRESETS:
        raise ApiError(422, "UNKNOWN_PRESET", "Unknown caption preset.", f"Use one of {sorted(PRESETS)}.")
    try:
        _, data = _read_transcript(svc, a)
    except ApiError:
        return {"preset": PRESETS[pid].to_json(), "groups": [], "note": "no transcript yet"}
    groups = group_words(data.get("words", []), lo / 1000, hi / 1000, PRESETS[pid])
    return {"preset": PRESETS[pid].to_json(), "groups": groups}
