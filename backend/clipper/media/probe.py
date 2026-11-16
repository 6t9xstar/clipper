"""ffprobe wrapper + validation of untrusted input files.

Safety: every FFmpeg/ffprobe call on untrusted input uses SAFE_INPUT_ARGS, which restricts demuxers to real
video containers and protocols to local files. This blocks playlist/concat tricks
(e.g. an HLS file pointing at other files on disk). MOV external references stay disabled
(FFmpeg default enable_drefs=0).
"""

from __future__ import annotations

import contextlib
import json
import math
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from clipper.jobs import proc
from clipper.media.errors import MediaError

DEMUXERS = "mov,matroska,webm,avi,mpegts,flv"
SAFE_INPUT_ARGS = ["-protocol_whitelist", "file", "-format_whitelist", DEMUXERS]
VIDEO_CODECS = {
    "h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "mpeg2video", "mpeg1video",
    "prores", "dnxhd", "mjpeg", "h263", "theora", "ffv1", "vc1", "wmv3",
}  # fmt: skip
MIN_SIDE, MAX_W, MAX_H = 64, 7680, 4320
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


@dataclass
class MediaInfo:
    duration_ms: int
    width: int  # display size (rotation applied)
    height: int
    coded_width: int
    coded_height: int
    rotation: int
    fps: float
    vfr: bool
    video_codec: str
    pix_fmt: str | None
    bit_depth: int
    hdr: bool
    interlaced: bool
    has_audio: bool
    audio_codec: str | None
    audio_channels: int | None
    audio_sample_rate: int | None
    container: str
    bitrate: int | None
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def tool(name: str) -> str:
    exe = shutil.which(name)
    if exe is None:
        from clipper.jobs.errors import JobError

        raise JobError(f"{name} was not found on PATH", code="FFMPEG_MISSING", hint="Run `clipper doctor`.")
    return exe


def _rate(s: str | None) -> float:
    if not s or "/" not in s:
        return 0.0
    n, d = s.split("/", 1)
    try:
        return float(n) / float(d) if float(d) else 0.0
    except ValueError:
        return 0.0


def _rotation(stream: dict[str, Any]) -> int:
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return round(float(sd["rotation"])) % 360
            except (TypeError, ValueError):
                pass
    rot = (stream.get("tags") or {}).get("rotate")
    try:
        return int(rot) % 360 if rot is not None else 0
    except ValueError:
        return 0


def ffprobe_json(path: Path, should_cancel: Callable[[], bool]) -> dict[str, Any]:
    argv = [
        tool("ffprobe"), "-v", "error", *SAFE_INPUT_ARGS,
        "-show_format", "-show_streams", "-of", "json", str(path),
    ]  # fmt: skip
    r = proc.run(argv, should_cancel=should_cancel, timeout=120)
    err = r.stderr.decode("utf-8", "replace")
    if "not on whitelist" in err:
        raise MediaError("MEDIA_UNSUPPORTED_CONTAINER", err[-300:])
    if r.returncode != 0:
        raise MediaError("MEDIA_UNREADABLE", err[-300:])
    try:
        data: dict[str, Any] = json.loads(r.stdout or b"{}")
    except json.JSONDecodeError as e:
        raise MediaError("MEDIA_UNREADABLE", f"bad ffprobe json: {e}") from e
    return data


def analyse(data: dict[str, Any], *, max_duration_s: float) -> MediaInfo:
    """Turn raw ffprobe JSON into validated MediaInfo, or raise MediaError. Pure function (unit-tested)."""
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    videos = [
        s for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]  # fmt: skip
    if not videos:
        raise MediaError("MEDIA_NO_VIDEO")
    v = videos[0]
    codec = str(v.get("codec_name") or "")
    if codec not in VIDEO_CODECS:
        raise MediaError("MEDIA_UNSUPPORTED_CODEC", codec)
    container = str(fmt.get("format_name") or "")
    if container.split(",")[0] in {"image2", "png_pipe", "jpeg_pipe", "gif", "webp_pipe"}:
        raise MediaError("MEDIA_NO_VIDEO", container)

    dur = 0.0
    for cand in (fmt.get("duration"), v.get("duration")):
        try:
            dur = float(cand) if cand is not None else 0.0
        except ValueError:
            dur = 0.0
        if dur > 0 and math.isfinite(dur):
            break
    if not (dur > 0 and math.isfinite(dur)):
        raise MediaError("MEDIA_UNREADABLE", "no duration")
    if dur < 1.0:
        raise MediaError("MEDIA_TOO_SHORT")
    if dur > max_duration_s:
        raise MediaError("MEDIA_TOO_LONG", f"{dur:.0f}s")

    cw, ch = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = _rotation(v)
    w, h = (ch, cw) if rot in (90, 270) else (cw, ch)
    if min(w, h) < MIN_SIDE or max(w, h) > MAX_W or min(w, h) > MAX_H:
        raise MediaError("MEDIA_RESOLUTION_UNSUPPORTED", f"{w}x{h}")

    avg, real = _rate(v.get("avg_frame_rate")), _rate(v.get("r_frame_rate"))
    fps = avg or real
    if not (0 < fps <= 1000):
        raise MediaError("MEDIA_UNREADABLE", f"fps={fps}")
    vfr = bool(avg and real and abs(avg - real) / real > 0.02)

    pix = v.get("pix_fmt")
    depth = int(v.get("bits_per_raw_sample") or 0) or (10 if pix and ("10" in pix or "p010" in pix) else 8)
    if pix and "12" in pix:
        depth = 12
    hdr = v.get("color_transfer") in HDR_TRANSFERS
    interlaced = (v.get("field_order") or "progressive") not in ("progressive", "unknown")

    audios = [s for s in streams if s.get("codec_type") == "audio"]
    a = audios[0] if audios else None
    warnings: list[str] = []
    if a is None:
        warnings.append("NO_AUDIO")
    if vfr:
        warnings.append("VARIABLE_FRAME_RATE")
    if hdr:
        warnings.append("HDR_COLORS_MAY_LOOK_WASHED_OUT")
    if interlaced:
        warnings.append("INTERLACED")
    if fps > 120:
        warnings.append("HIGH_FRAME_RATE")
    try:
        bitrate = int(fmt["bit_rate"]) if fmt.get("bit_rate") else None
    except ValueError:
        bitrate = None
    return MediaInfo(
        duration_ms=round(dur * 1000), width=w, height=h, coded_width=cw, coded_height=ch, rotation=rot,
        fps=round(fps, 3), vfr=vfr, video_codec=codec, pix_fmt=pix, bit_depth=depth, hdr=hdr,
        interlaced=interlaced, has_audio=a is not None, audio_codec=(a or {}).get("codec_name"),
        audio_channels=int(a["channels"]) if a and a.get("channels") else None,
        audio_sample_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else None,
        container=container, bitrate=bitrate, warnings=warnings,
    )  # fmt: skip


def decode_check(path: Path, info: MediaInfo, should_cancel: Callable[[], bool]) -> None:
    """Decode ~2 s at the start and at the end. Catches truncated downloads and broken streams cheaply.

    Also measures the real frame rate in both windows: containers like MKV report a nominal rate even for
    variable-frame-rate video, so this is the reliable (and free) place to detect VFR.
    """
    dur = info.duration_ms / 1000
    window = min(2.0, dur)
    for label, start in (("head", 0.0), ("tail", max(0.0, dur - 3.0))):
        argv = [
            tool("ffmpeg"), "-v", "error", "-nostdin", *SAFE_INPUT_ARGS,
            "-ss", f"{start:.3f}", "-i", str(path),
            "-map", "0:v:0", "-t", f"{window:.3f}", "-fps_mode", "passthrough",
            "-f", "null", "-progress", "pipe:1", "-",
        ]  # fmt: skip
        frames = [0]

        def line(s: str, frames: list[int] = frames) -> None:
            if s.startswith("frame="):
                with contextlib.suppress(ValueError):
                    frames[0] = int(s[6:])

        r = proc.run(argv, should_cancel=should_cancel, timeout=300, on_stdout_line=line)
        if r.returncode != 0:
            raise MediaError("MEDIA_CORRUPT", f"{label}: {r.stderr.decode('utf-8', 'replace')[-300:]}")
        if frames[0] == 0:
            raise MediaError("MEDIA_TRUNCATED" if label == "tail" else "MEDIA_CORRUPT", f"{label}: no frames")
        measured = frames[0] / window
        if (
            window >= 1.0
            and abs(measured - info.fps) / info.fps > 0.15
            and "VARIABLE_FRAME_RATE" not in info.warnings
        ):
            info.vfr = True
            info.warnings.append("VARIABLE_FRAME_RATE")
