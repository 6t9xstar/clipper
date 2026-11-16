"""Edit decision -> FFmpeg argv. Pure functions (unit-tested); running them is clipper.render.export's job.

Video:  source range -> deinterlace? -> 9:16 crop (edit.framing) or whole frame over a blurred copy
        -> 1080x1920 (or 720x1280) -> constant frame rate (max 60) -> burned captions (libass)
        -> yuv420p BT.709
Audio:  EBU R128 two-pass loudness normalisation to -14 LUFS / -1.5 dBTP, 48 kHz stereo AAC.
The caption script and the bundled fonts are copied into the job's work folder and referenced by relative
names (FFmpeg runs with that folder as cwd), so no file path ever has to be escaped inside a filter graph.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from clipper.edit.framing import crop_rect
from clipper.media.probe import SAFE_INPUT_ARGS, tool

TARGET_LUFS = -14.0
TARGET_TP = -1.5
TARGET_LRA = 11.0
SILENT_LUFS = -60.0  # quieter than this: don't amplify (it would only raise noise)
MAX_FPS = 60.0
ASS_NAME = "captions.ass"
FONTS_NAME = "fonts"


@dataclass(frozen=True)
class Quality:
    id: str
    name: str
    description: str
    width: int
    height: int
    crf: int
    x264_preset: str
    audio_kbps: int
    max_kbps: int  # video bitrate ceiling: grainy or dark footage must not balloon the file

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


QUALITIES: dict[str, Quality] = {
    q.id: q
    for q in (
        Quality(
            "standard", "Standard 1080p",
            "1080 x 1920 H.264/AAC. The usual choice for Shorts, TikTok and Reels.",
            1080, 1920, 20, "veryfast", 192, 16_000,
        ),
        Quality(
            "high", "High 1080p",
            "1080 x 1920 with more detail and a bigger file; encodes about 2-3 times slower.",
            1080, 1920, 17, "medium", 256, 24_000,
        ),
        Quality(
            "draft", "Draft 720p",
            "720 x 1280, fastest; for checking a cut before the final export.",
            720, 1280, 26, "ultrafast", 128, 6_000,
        ),
    )
}  # fmt: skip
DEFAULT_QUALITY = "standard"


@dataclass(frozen=True)
class Source:
    path: Path
    width: int  # display size (rotation applied by FFmpeg before filters)
    height: int
    fps: float
    interlaced: bool
    has_audio: bool


def out_fps(src_fps: float) -> float:
    f = src_fps if src_fps > 0 else 30.0
    return round(min(f, MAX_FPS), 3)


def _num(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".")


def video_filter(src: Source, edit: dict[str, Any], q: Quality, captions: bool) -> str:
    fr = edit["framing"]
    fps = _num(out_fps(src.fps))
    pre = "bwdif=deint=interlaced," if src.interlaced else ""
    tail = (f",ass={ASS_NAME}:fontsdir={FONTS_NAME}" if captions else "") + ",format=yuv420p,setsar=1"
    if fr["mode"] == "fit":
        # mirrors the preview: 90x160 canvas, blur(10px) brightness(0.6), stretched to the frame
        return (
            f"[0:v:0]{pre}fps={fps},split=2[a][b];"
            "[a]scale=90:160:force_original_aspect_ratio=increase,crop=90:160,gblur=sigma=10,"
            f"colorchannelmixer=rr=0.6:gg=0.6:bb=0.6,scale={q.width}:{q.height}:flags=bilinear[bg];"
            f"[b]scale={q.width}:{q.height}:force_original_aspect_ratio=decrease:force_divisible_by=2"
            ":flags=lanczos:out_color_matrix=bt709[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2{tail}[v]"
        )
    r = crop_rect(src.width, src.height, fr["x"], fr["y"], fr["zoom"])
    w = min(src.width, max(2, round(r.w)))
    h = min(src.height, max(2, round(r.h)))
    x = min(max(0, round(r.x)), src.width - w)
    y = min(max(0, round(r.y)), src.height - h)
    return (
        f"[0:v:0]{pre}crop={w}:{h}:{x}:{y},"
        f"scale={q.width}:{q.height}:flags=lanczos:out_color_matrix=bt709,fps={fps}{tail}[v]"
    )


def _input(src: Source, start: float, dur: float) -> list[str]:
    # -ss before -i: fast seek; with re-encoding FFmpeg still cuts frame-accurately
    return [*SAFE_INPUT_ARGS, "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src.path)]


def loudness_cmd(src: Source, start: float, dur: float) -> list[str]:
    """First pass: measure the clip's loudness (printed as JSON on stderr)."""
    return [
        tool("ffmpeg"),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        *_input(src, start, dur),
        "-map",
        "0:a:0",
        "-af",
        f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
        "-f",
        "null",
        "-",
    ]


def parse_loudnorm(stderr: str) -> dict[str, float] | None:
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        out = {k: float(d[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (KeyError, ValueError):
        return None
    return out if all(math.isfinite(v) for v in out.values()) else None


@dataclass(frozen=True)
class AudioPlan:
    """How the clip's audio is brought to the loudness target.

    linear:    plain gain (peaks stay below the true-peak ceiling) - transparent
    limited:   gain into a 4x-oversampled peak limiter, at most MAX_LIMITING_DB of peak reduction; if even
               that cannot reach the target the export is left quieter and the shortfall is reported,
               because squashing speech harder audibly damages it
    untouched: no audio worth normalising (near-silence) or nothing measured
    """

    mode: str
    gain_db: float
    capped: bool = False

    @property
    def filter(self) -> str:
        if self.mode == "untouched":
            return "aresample=48000"
        vol = f"volume={self.gain_db:.2f}dB"
        if self.mode == "linear":
            return f"{vol},aresample=48000"
        limiter = f"alimiter=limit={LIMIT_LINEAR}:attack=5:release=80:level=false"
        return f"{vol},aresample=192000,{limiter},aresample=48000"


MAX_LIMITING_DB = 8.0
LIMIT_LINEAR = 0.7943  # -2 dBFS sample peak at 192 kHz ~ true peak just under -1.5 dBTP


def audio_plan(measured: dict[str, float] | None, gain_db: float | None = None) -> AudioPlan:
    if measured is None or measured["input_i"] < SILENT_LUFS:
        return AudioPlan("untouched", 0.0)
    want = TARGET_LUFS - measured["input_i"] if gain_db is None else gain_db
    headroom = TARGET_TP - measured["input_tp"]  # gain possible before peaks cross the ceiling
    if want <= headroom:
        return AudioPlan("linear", round(want, 2))
    cap = headroom + MAX_LIMITING_DB
    return AudioPlan("limited", round(min(want, cap), 2), capped=want > cap)


def measure_cmd(src: Source, start: float, dur: float, audio_filter: str) -> list[str]:
    """Loudness of the clip after `audio_filter` (used to refine the gain when the limiter is involved)."""
    return [
        tool("ffmpeg"), "-hide_banner", "-nostdin", "-nostats", *_input(src, start, dur), "-map", "0:a:0",
        "-af", f"{audio_filter},ebur128=peak=true:framelog=quiet", "-f", "null", "-",
    ]  # fmt: skip


def render_cmd(
    src: Source,
    start: float,
    dur: float,
    edit: dict[str, Any],
    q: Quality,
    *,
    captions: bool,
    audio: AudioPlan,
    out: Path,
) -> list[str]:
    fps = out_fps(src.fps)
    gop = max(1, round(fps * 2))
    argv = [
        tool("ffmpeg"),
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-y",
        *_input(src, start, dur),
        "-filter_complex",
        video_filter(src, edit, q, captions),
        "-map",
        "[v]",
    ]
    if src.has_audio:
        argv += [
            "-map",
            "0:a:0",
            "-af",
            audio.filter,
            "-c:a",
            "aac",
            "-b:a",
            f"{q.audio_kbps}k",
            "-ac",
            "2",
            "-ar",
            "48000",
        ]
    argv += [
        "-c:v",
        "libx264",
        "-preset",
        q.x264_preset,
        "-crf",
        str(q.crf),
        "-maxrate",
        f"{q.max_kbps}k",
        "-bufsize",
        f"{q.max_kbps * 2}k",
        "-profile:v",
        "high",
        "-level:v",
        "4.2",
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(gop),
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        # nothing from the source file's metadata (camera, GPS, encoder tags) is copied into the export
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-metadata",
        "comment=Made with Clipper",
        "-t",
        f"{dur:.3f}",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(out),
        "-progress",
        "pipe:1",
        "-stats_period",
        "0.5",
    ]
    return argv


def ebur128_cmd(path: Path) -> list[str]:
    """Integrated loudness and true peak of a rendered file."""
    return [
        tool("ffmpeg"),
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-af",
        "ebur128=peak=true:framelog=quiet",
        "-f",
        "null",
        "-",
    ]


def parse_ebur128(stderr: str) -> float | None:
    """Integrated loudness from the ebur128 summary ("I:   -14.0 LUFS")."""
    found = re.findall(r"I:\s+(-?\d+(?:\.\d+)?|-inf)\s+LUFS", stderr)
    if not found or found[-1] == "-inf":
        return None
    return float(found[-1])


def parse_true_peak(stderr: str) -> float | None:
    """True peak from the ebur128 summary ("Peak:   -1.6 dBFS"), needs peak=true."""
    found = re.findall(r"Peak:\s+(-?\d+(?:\.\d+)?|-inf)\s+dBFS", stderr)
    if not found or found[-1] == "-inf":
        return None
    return float(found[-1])
