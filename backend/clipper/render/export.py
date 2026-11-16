"""`render.export` job: render one clip to a vertical MP4, verify it, and write sidecars and a manifest.

Steps:
  1. caption script (ASS) + bundled fonts into a private work folder
  2. loudness measurement of the clip's audio (EBU R128 first pass)
  3. render (crop/fit, captions, loudness normalisation, H.264/AAC)  -> clip.mp4
  4. verification with ffprobe and ebur128: codec, size, duration within one frame, audio present,
     integrated loudness within 1 LU of the target (when the source has real sound)
  5. clip.srt / clip.vtt (when there are captions) and clip.json (what was rendered, rights declaration)
Everything the render needs was snapshotted into `exports.settings` when the export was requested, so later
edits to the clip or transcript never change an export that is already queued.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clipper import __version__
from clipper.core.logging import log
from clipper.db.models import Asset, Export, ExportStatus
from clipper.edit.captions import PRESETS
from clipper.jobs import proc
from clipper.jobs.errors import JobCancelled, JobError
from clipper.jobs.worker import JobContext, handler
from clipper.media import probe
from clipper.media.importer import asset_prefix
from clipper.render import ass, fontinfo, graph, subs

LOUDNESS_TOLERANCE = 1.0  # LU


def export_prefix(project_id: str, asset_id: str, export_id: str) -> str:
    return f"{asset_prefix(project_id, asset_id)}/exports/{export_id}"


def _update(ctx: JobContext, export_id: str, **values: Any) -> None:
    with ctx.svc.db.session() as s:
        e = s.get(Export, export_id)
        if e is not None:
            for k, v in values.items():
                setattr(e, k, v)


def _fail(ctx: JobContext, export_id: str, code: str, message: str) -> None:
    _update(
        ctx,
        export_id,
        status=ExportStatus.FAILED,
        error_code=code,
        error_message=message[:500],
        finished_at=time.time(),
    )


def verify(
    path: Path,
    *,
    expect_w: int,
    expect_h: int,
    expect_dur: float,
    fps: float,
    has_audio: bool,
    loud: float | None,
    peak: float | None,
    audio: graph.AudioPlan,
    should_cancel: Any,
) -> dict[str, Any]:
    """Facts about the rendered file and pass/fail per check. Raises JobError if a structural check fails
    (codec, size, duration, audio track). Loudness is reported, not fatal: a slightly quiet clip is still
    a usable video, and the shortfall is shown to the user."""
    data = probe.ffprobe_json(path, should_cancel)
    streams = data.get("streams") or []
    v: dict[str, Any] = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    frame = 1 / fps
    vdur = float(v.get("duration") or data.get("format", {}).get("duration") or 0)
    checks: dict[str, Any] = {
        "video_codec": {"ok": v.get("codec_name") == "h264", "value": v.get("codec_name")},
        "size": {
            "ok": (v.get("width"), v.get("height")) == (expect_w, expect_h),
            "value": f"{v.get('width')}x{v.get('height')}",
        },
        "duration": {
            "ok": abs(vdur - expect_dur) <= frame + 0.001,
            "value": round(vdur, 3),
            "expected": round(expect_dur, 3),
            "tolerance": round(frame, 4),
        },
        "audio": {
            "ok": (a is not None and a.get("codec_name") == "aac") if has_audio else a is None,
            "value": (a or {}).get("codec_name"),
        },
    }
    if has_audio:
        note = None
        if audio.mode == "untouched":
            note = "The source is (nearly) silent, so its level was left as is."
        elif audio.capped:
            note = (
                f"Quieter than {graph.TARGET_LUFS:g} LUFS: reaching it would need more than "
                f"{graph.MAX_LIMITING_DB:g} dB of peak limiting, which audibly squashes speech."
            )
        on_target = loud is not None and abs(loud - graph.TARGET_LUFS) <= LOUDNESS_TOLERANCE
        checks["loudness"] = {
            "ok": on_target or audio.mode == "untouched",
            "fatal": False,
            "value": loud,
            "target": graph.TARGET_LUFS,
            "true_peak": peak,
            "mode": audio.mode,
            "gain_db": audio.gain_db,
            "note": note,
        }
    bad = [k for k, c in checks.items() if not c["ok"] and c.get("fatal", True)]
    if bad:
        raise JobError(
            f"export failed verification: {', '.join(bad)} ({checks})",
            code="EXPORT_VERIFY_FAILED",
            hint="This is a bug; please report it with the job details.",
        )
    return checks


@handler("render.export")
def render_export(ctx: JobContext) -> dict[str, Any]:
    export_id = str(ctx.payload["export_id"])
    svc = ctx.svc
    with svc.db.session() as s:
        e = s.get(Export, export_id)
        if e is None:
            raise JobError("export was deleted", code="EXPORT_GONE")
        a = s.get(Asset, e.asset_id)
        if a is None:
            raise JobError("video was deleted", code="ASSET_GONE")
        st: dict[str, Any] = dict(e.settings)
        project_id, asset_id, quality = e.project_id, e.asset_id, e.quality
        rights = {
            "type": a.rights_type,
            "note": a.rights_note,
            "confirmed_at": datetime.fromtimestamp(a.rights_confirmed_at, UTC).isoformat(),
        }
        source_name, sha = a.display_name, a.sha256
        info = (a.media_info or {}).get("media") or {}
        src = graph.Source(
            path=svc.storage.path(f"{asset_prefix(project_id, asset_id)}/source"),
            width=int(a.width or 0),
            height=int(a.height or 0),
            fps=float(a.fps or 30.0),
            interlaced=bool(info.get("interlaced")),
            has_audio=bool(a.has_audio),
        )
        e.status = ExportStatus.RENDERING
    if not src.path.exists():
        _fail(ctx, export_id, "SOURCE_MISSING", "The source video file is missing.")
        raise JobError("source file is missing", code="SOURCE_MISSING")

    q = graph.QUALITIES[quality]
    start, end = st["start_ms"] / 1000, st["end_ms"] / 1000
    dur = end - start
    edit = st["edit"]
    groups: list[dict[str, Any]] = st.get("groups") or []
    captions = bool(edit["captions"]["enabled"] and groups)
    preset = PRESETS[edit["captions"]["preset"]]

    prefix = export_prefix(project_id, asset_id, export_id)
    out_dir = svc.storage.path(prefix)
    work = svc.storage.path(f"{prefix}/work")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        ctx.progress(0.01, "Preparing captions")
        if captions:
            (work / graph.ASS_NAME).write_text(
                ass.build(groups, preset, edit["captions"]["position"], dur), encoding="utf-8"
            )
            fonts = work / graph.FONTS_NAME
            fonts.mkdir()
            shutil.copy2(fontinfo.FONTS_DIR / preset.font_file, fonts / preset.font_file)

        measured = None
        if src.has_audio:
            ctx.progress(0.03, "Measuring loudness")
            r = proc.run(
                graph.loudness_cmd(src, start, dur),
                should_cancel=ctx.is_cancelled,
                timeout=max(300.0, dur * 4),
            )
            if r.returncode != 0:
                raise JobError(
                    "loudness measurement failed: " + r.stderr.decode("utf-8", "replace")[-300:],
                    code="RENDER_FAILED",
                )
            measured = graph.parse_loudnorm(r.stderr.decode("utf-8", "replace"))
        audio = graph.audio_plan(measured)
        if audio.mode == "limited":
            # the limiter takes some loudness back: measure and correct the gain (audio only, fast)
            for _ in range(3):
                ctx.check_cancel()
                r = proc.run(graph.measure_cmd(src, start, dur, audio.filter), should_cancel=ctx.is_cancelled,
                             timeout=max(300.0, dur * 4))  # fmt: skip
                got = graph.parse_ebur128(r.stderr.decode("utf-8", "replace"))
                if got is None or abs(got - graph.TARGET_LUFS) <= 0.3:
                    break
                nxt = graph.audio_plan(measured, audio.gain_db + graph.TARGET_LUFS - got)
                if nxt.gain_db == audio.gain_db:
                    break  # capped: as loud as it gets without squashing
                audio = nxt

        part = work / "clip.mp4"
        total_us = dur * 1_000_000

        def line(s: str) -> None:
            if s.startswith("out_time_us="):
                try:
                    f = int(s[12:]) / total_us
                except ValueError:
                    return
                ctx.report(0.08 + 0.82 * min(1.0, max(0.0, f)), "Rendering")

        ctx.progress(0.08, "Rendering")
        argv = graph.render_cmd(src, start, dur, edit, q, captions=captions, audio=audio, out=part)
        r = proc.run(
            argv,
            should_cancel=ctx.is_cancelled,
            timeout=max(600.0, dur * 30),
            cwd=str(work),
            on_stdout_line=line,
        )
        if r.returncode != 0 or not part.exists():
            raise JobError(
                "FFmpeg failed: " + r.stderr.decode("utf-8", "replace")[-500:], code="RENDER_FAILED"
            )
        ctx.check_cancel()

        ctx.progress(0.92, "Checking the result")
        loud = peak = None
        if src.has_audio:
            r = proc.run(graph.ebur128_cmd(part), should_cancel=ctx.is_cancelled, timeout=max(300.0, dur * 4))
            loud = graph.parse_ebur128(r.stderr.decode("utf-8", "replace"))
            peak = graph.parse_true_peak(r.stderr.decode("utf-8", "replace"))
        fps = graph.out_fps(src.fps)
        checks = verify(
            part,
            expect_w=q.width,
            expect_h=q.height,
            expect_dur=dur,
            fps=fps,
            has_audio=src.has_audio,
            loud=loud,
            peak=peak,
            audio=audio,
            should_cancel=ctx.is_cancelled,
        )

        files: dict[str, Path] = {"mp4": part}
        if groups:
            (work / "clip.srt").write_text(subs.srt(groups), encoding="utf-8")
            (work / "clip.vtt").write_text(subs.vtt(groups), encoding="utf-8")
            files["srt"], files["vtt"] = work / "clip.srt", work / "clip.vtt"
        manifest = {
            "generator": f"Clipper {__version__}",
            "created_at": datetime.now(UTC).isoformat(),
            "export_id": export_id,
            "source": {"asset_id": asset_id, "file_name": source_name, "sha256": sha},
            "rights": rights,
            "clip": {
                "id": st["clip_id"],
                "start_ms": st["start_ms"],
                "end_ms": st["end_ms"],
                "title": st.get("title"),
            },
            "video": {
                "width": q.width,
                "height": q.height,
                "fps": fps,
                "codec": "H.264 (libx264)",
                "quality": quality,
                "framing": edit["framing"],
            },
            "audio": {
                "codec": "AAC",
                "sample_rate": 48000,
                "loudness_target_lufs": graph.TARGET_LUFS,
                "loudness_lufs": loud,
                "source_loudness_lufs": measured["input_i"] if measured else None,
                "true_peak_dbtp": peak,
                "processing": {"mode": audio.mode, "gain_db": audio.gain_db},
            }
            if src.has_audio
            else None,
            "captions": {
                "burned_in": captions,
                "preset": preset.id,
                "font": preset.font.family,
                "screens": len(groups),
                "transcript": st.get("transcript"),
            },
            "settings_hash": st.get("hash"),
            "checks": checks,
        }
        (work / "clip.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        files["json"] = work / "clip.json"

        sizes: dict[str, int] = {}
        for kind, f in files.items():
            os.replace(f, out_dir / f"clip.{kind}")
            sizes[kind] = (out_dir / f"clip.{kind}").stat().st_size
    except JobCancelled:
        _update(ctx, export_id, status=ExportStatus.CANCELLED, finished_at=time.time())
        raise
    except JobError as err:
        if not err.retryable:
            _fail(ctx, export_id, err.code or "RENDER_FAILED", str(err))
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    _update(
        ctx,
        export_id,
        status=ExportStatus.DONE,
        files=sizes,
        duration_ms=round(dur * 1000),
        width=q.width,
        height=q.height,
        fps=fps,
        loudness_lufs=loud,
        checks=checks,
        error_code=None,
        error_message=None,
        finished_at=time.time(),
    )
    log.info("export.done", export=export_id, seconds=round(dur, 2), quality=quality)
    ctx.progress(1.0, "Done")
    return {"export_id": export_id, "files": sizes, "loudness_lufs": loud}
