"""One decode pass over the source -> editor proxy, thumbnail sprite, ASR audio; then a waveform.

Memory stays bounded for any input length: FFmpeg streams; the waveform reads the WAV in blocks.
"""

from __future__ import annotations

import array
import contextlib
import math
import os
import sys
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clipper.jobs import proc
from clipper.media.errors import MediaError
from clipper.media.probe import SAFE_INPUT_ARGS, MediaInfo, tool

PROXY_SHORT_SIDE = 540
PROXY_MAX_FPS = 30.0
THUMB_W = 160
MAX_THUMBS = 120
WAVEFORM_RATE = 20  # peaks per second
ASR_RATE = 16000


@dataclass(frozen=True)
class ThumbSpec:
    count: int
    interval_s: float
    cols: int
    rows: int
    tile_w: int
    tile_h: int

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _even(x: float) -> int:
    return max(2, round(x / 2) * 2)


def proxy_size(info: MediaInfo) -> tuple[int, int]:
    w, h = info.width, info.height
    short = min(w, h)
    scale = min(1.0, PROXY_SHORT_SIDE / short)
    return _even(w * scale), _even(h * scale)


def thumb_spec(info: MediaInfo) -> ThumbSpec:
    dur = info.duration_ms / 1000
    count = max(1, min(MAX_THUMBS, math.ceil(dur / 2)))
    cols = min(10, count)
    rows = math.ceil(count / cols)
    th = _even(THUMB_W * info.height / info.width)
    return ThumbSpec(count, dur / count, cols, rows, THUMB_W, th)


def build_prepare_cmd(
    src: Path, info: MediaInfo, out_dir: Path, tmp: str
) -> tuple[list[str], dict[str, Path]]:
    """Return argv and {artifact: temp path}. Pure (unit-tested)."""
    pw, ph = proxy_size(info)
    fps = min(info.fps, PROXY_MAX_FPS)
    fps_s = f"{fps:.3f}".rstrip("0").rstrip(".")
    gop = max(1, round(fps))  # keyframe every ~1 s -> fast, accurate scrubbing
    ts = thumb_spec(info)
    deint = "bwdif=deint=interlaced," if info.interlaced else ""
    fc = (
        f"[0:v:0]{deint}scale={pw}:{ph}:flags=bicubic,fps={fps_s},format=yuv420p,setsar=1,split=2[pv][tv];"
        f"[tv]fps=1/{ts.interval_s:.6f},scale={ts.tile_w}:{ts.tile_h},tile={ts.cols}x{ts.rows}[th]"
    )
    paths = {
        "proxy": out_dir / f".part-{tmp}-proxy.mp4",
        "thumbs": out_dir / f".part-{tmp}-thumbs.jpg",
    }
    argv = [
        tool("ffmpeg"), "-v", "error", "-nostdin", "-y", *SAFE_INPUT_ARGS, "-i", str(src),
        "-filter_complex", fc,
        # proxy
        "-map", "[pv]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-ar", "48000",
        "-movflags", "+faststart", "-f", "mp4", str(paths["proxy"]),
        # thumbnail sprite
        "-map", "[th]", "-frames:v", "1", "-q:v", "5", "-update", "1", "-f", "image2", str(paths["thumbs"]),
    ]  # fmt: skip
    if info.has_audio:
        paths["audio"] = out_dir / f".part-{tmp}-audio.wav"
        argv += [
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(ASR_RATE),
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(paths["audio"]),
        ]
    argv += ["-progress", "pipe:1", "-stats_period", "1"]
    return argv, paths


def run_prepare(
    src: Path,
    info: MediaInfo,
    out_dir: Path,
    *,
    should_cancel: Callable[[], bool],
    on_progress: Callable[[float], None],
) -> dict[str, Path]:
    """Produce proxy.mp4, thumbs.jpg, audio.wav (if any) in out_dir. Idempotent: skips if all exist."""
    final = {"proxy": out_dir / "proxy.mp4", "thumbs": out_dir / "thumbs.jpg"}
    if info.has_audio:
        final["audio"] = out_dir / "audio.wav"
    if all(p.exists() for p in final.values()):
        return final
    out_dir.mkdir(parents=True, exist_ok=True)
    argv, tmp_paths = build_prepare_cmd(src, info, out_dir, os.urandom(4).hex())
    total_us = info.duration_ms * 1000

    def line(s: str) -> None:
        if s.startswith("out_time_us="):
            with contextlib.suppress(ValueError):
                on_progress(min(1.0, max(0.0, int(s[12:]) / total_us)))

    try:
        r = proc.run(argv, should_cancel=should_cancel, timeout=max(600.0, info.duration_ms / 1000 * 4),
                     on_stdout_line=line)  # fmt: skip
        if r.returncode != 0:
            raise MediaError("MEDIA_PROCESSING_FAILED", r.stderr.decode("utf-8", "replace")[-500:])
        for key, tmp in tmp_paths.items():
            if not tmp.exists() or tmp.stat().st_size == 0:
                raise MediaError("MEDIA_PROCESSING_FAILED", f"missing output {key}")
        for key, tmp in tmp_paths.items():
            os.replace(tmp, final[key])
    finally:
        for tmp in tmp_paths.values():
            tmp.unlink(missing_ok=True)
    return final


def waveform(wav_path: Path, *, rate: int = WAVEFORM_RATE, should_cancel: Callable[[], bool]) -> list[int]:
    """Peak amplitude (0..255) per 1/rate s, read in blocks: O(1) memory in file length."""
    peaks: list[int] = []
    with wave.open(str(wav_path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise MediaError("MEDIA_PROCESSING_FAILED", "unexpected wav format")
        per = max(1, w.getframerate() // rate)
        block = per * rate * 10  # 10 s per read
        while True:
            raw = w.readframes(block)
            if not raw:
                break
            a = array.array("h")
            a.frombytes(raw)
            if sys.byteorder == "big":
                a.byteswap()
            for i in range(0, len(a), per):
                seg = a[i : i + per]
                peak = max(max(seg), -min(seg))
                peaks.append(min(255, peak * 255 // 32767))
            if should_cancel():
                from clipper.jobs.errors import JobCancelled

                raise JobCancelled()
    return peaks
