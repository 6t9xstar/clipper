"""`ai.analyze` job: transcript -> shot cuts -> highlight suggestions.

Each expensive step is cached in the asset's derived/ folder, so a retry after a crash, or re-running the
analysis with different clip lengths, does not transcribe again (pass "retranscribe": true to force it).

Clip replacement rule: only clips that are still plain AI suggestions (origin=ai, status=suggested) are
replaced. Approved, rejected and manual clips are kept, and new suggestions overlapping them by more than half
are dropped.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from clipper.ai import asr, highlights, scenes
from clipper.core.logging import log
from clipper.db.models import Asset, AssetStatus, Clip, ClipStatus, Transcript
from clipper.jobs.errors import JobError
from clipper.jobs.worker import JobContext, handler
from clipper.media.importer import asset_prefix

TITLE_WORDS = 9


def _model_cached(models_dir: Path, model: str) -> bool:
    return any(models_dir.glob(f"models--*--faster-whisper-{model}"))


def _overlap(a0: int, a1: int, b0: int, b1: int) -> float:
    inter = max(0, min(a1, b1) - max(a0, b0))
    return inter / max(1, min(a1 - a0, b1 - b0))


def _title(text: str) -> str | None:
    words = text.split()
    if not words:
        return None
    t = " ".join(words[:TITLE_WORDS])
    return (t + "…" if len(words) > TITLE_WORDS else t)[:200]


def _transcribe(ctx: JobContext, a: Asset, derived: Path, prefix: str, force: bool) -> dict[str, Any]:
    svc = ctx.svc
    tj = derived / "transcript.json"
    if tj.exists() and not force:
        with svc.db.session() as s:
            if s.get(Transcript, a.id) is not None:
                cached: dict[str, Any] = json.loads(tj.read_text(encoding="utf-8"))
                return cached
    if not a.has_audio:
        return {"language": None, "words": [], "notes": ["video has no audio track"]}
    audio = derived / "audio.wav"
    if not audio.exists():
        raise JobError(
            "prepared audio is missing",
            code="ARTIFACT_MISSING",
            hint="Delete this video and import it again.",
        )
    st = svc.settings
    model, _, _ = asr.resolve(st.asr_model, st.asr_device)
    msg = (
        "Transcribing"
        if _model_cached(st.models_dir, model)
        else f"Downloading speech model '{model}' (first run)"
    )
    ctx.progress(0.02, msg)
    t0 = time.time()
    res = asr.transcribe(
        audio,
        model_setting=st.asr_model,
        device_setting=st.asr_device,
        models_dir=st.models_dir,
        cpu_threads=st.asr_cpu_threads,
        language=ctx.payload.get("language"),
        on_progress=lambda f: ctx.progress(0.03 + 0.72 * f, "Transcribing"),
        should_cancel=ctx.is_cancelled,
    )
    took = time.time() - t0
    data = res.to_json()
    with svc.storage.open_write(f"{prefix}/derived/transcript.json") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    with svc.db.session() as s:
        row = s.get(Transcript, a.id) or Transcript(asset_id=a.id)
        row.language, row.language_probability = res.language, res.language_probability
        row.model, row.device, row.word_count = res.model, res.device, len(res.words)
        row.seconds_taken, row.edited, row.created_at = round(took, 2), False, time.time()
        s.merge(row)
    log.info(
        "asr.done",
        asset=a.id,
        model=res.model,
        device=res.device,
        words=len(res.words),
        seconds=round(took, 1),
        audio_seconds=round(res.duration, 1),
    )
    return data


def _cuts(ctx: JobContext, a: Asset, derived: Path, prefix: str) -> list[float]:
    sj = derived / "scenes.json"
    if sj.exists():
        cached: list[float] = json.loads(sj.read_text())["cuts"]
        return cached
    proxy = derived / "proxy.mp4"
    if not proxy.exists():
        return []
    ctx.progress(0.76, "Finding shot changes")
    dur = (a.duration_ms or 0) / 1000
    cuts = scenes.detect_cuts(
        proxy,
        dur,
        should_cancel=ctx.is_cancelled,
        on_progress=lambda f: ctx.progress(0.76 + 0.14 * f, "Finding shot changes"),
    )
    with ctx.svc.storage.open_write(f"{prefix}/derived/scenes.json") as fh:
        fh.write(json.dumps({"threshold": scenes.THRESHOLD, "cuts": cuts}).encode())
    return cuts


@handler("ai.analyze")
def analyze(ctx: JobContext) -> dict[str, Any]:
    asset_id = str(ctx.payload["asset_id"])
    svc = ctx.svc
    params = highlights.Params(
        min_s=float(ctx.payload.get("min_seconds", 15)),
        max_s=float(ctx.payload.get("max_seconds", 60)),
        count=int(ctx.payload.get("count", 10)),
    )
    with svc.db.session() as s:
        a = s.get(Asset, asset_id)
        if a is None:
            raise JobError("asset was deleted", code="ASSET_GONE")
        if a.status != AssetStatus.READY:
            raise JobError("video is not ready", code="ASSET_NOT_READY")
        s.expunge(a)
    prefix = asset_prefix(a.project_id, a.id)
    derived = svc.storage.path(f"{prefix}/derived")

    tr = _transcribe(ctx, a, derived, prefix, bool(ctx.payload.get("retranscribe")))
    ctx.check_cancel()
    cuts = _cuts(ctx, a, derived, prefix)
    ctx.progress(0.92, "Finding highlights")
    peaks: list[int] = []
    rate = 0.0
    wf = derived / "waveform.json"
    if wf.exists():
        w = json.loads(wf.read_text())
        peaks, rate = w["peaks"], float(w["rate"])
    res = highlights.find_highlights(
        tr["words"],
        duration=(a.duration_ms or 0) / 1000,
        language=tr.get("language"),
        cuts=cuts,
        peaks=peaks,
        peaks_rate=rate,
        params=params,
    )
    ctx.check_cancel()

    with svc.db.session() as s:
        s.execute(
            delete(Clip).where(
                Clip.asset_id == a.id, Clip.origin == "ai", Clip.status == ClipStatus.SUGGESTED
            )
        )
        kept = [(c.start_ms, c.end_ms) for c in s.scalars(select(Clip).where(Clip.asset_id == a.id))]
        rank = 0
        for c in res.candidates:
            s0, s1 = round(c.start * 1000), round(c.end * 1000)
            if any(_overlap(s0, s1, k0, k1) > 0.5 for k0, k1 in kept):
                continue
            rank += 1
            s.add(
                Clip(
                    project_id=a.project_id,
                    asset_id=a.id,
                    start_ms=s0,
                    end_ms=s1,
                    origin="ai",
                    status=ClipStatus.SUGGESTED,
                    score=round(c.score, 3),
                    rank=rank,
                    reasons=c.reasons,
                    features={k: round(v, 3) for k, v in c.features.items()},
                    title=_title(c.text),
                    edit={},
                )
            )
    ctx.progress(1.0, "Done")
    return {
        "asset_id": a.id,
        "mode": res.mode,
        "suggested": rank,
        "considered": res.considered,
        "language": tr.get("language"),
        "words": len(tr["words"]),
        "cuts": len(cuts),
        "notes": [*tr.get("notes", []), *res.notes],
    }
