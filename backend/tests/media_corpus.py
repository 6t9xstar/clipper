"""Generates a small media corpus with FFmpeg (no third-party media). Each entry: name -> path."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

FF = shutil.which("ffmpeg") or "ffmpeg"


def _ff(*args: str) -> None:
    subprocess.run([FF, "-v", "error", "-y", *args], check=True, timeout=120)


def _supports_display_rotation() -> bool:
    out = subprocess.run(
        [FF, "-hide_banner", "-h", "full"], capture_output=True, text=True, timeout=60
    ).stdout
    return "-display_rotation" in out


def _encoders() -> str:
    return subprocess.run(
        [FF, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=60
    ).stdout


V = ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d={d}"]
A = ["-f", "lavfi", "-i", "sine=f=440:sample_rate=48000:d={d}"]


def _src(d: float, audio: bool = True) -> list[str]:
    args = [x.format(d=d) for x in V]
    if audio:
        args += [x.format(d=d) for x in A]
    return args


def build(dir_: Path) -> dict[str, Path]:
    dir_.mkdir(parents=True, exist_ok=True)
    c: dict[str, Path] = {}
    enc = _encoders()

    def p(name: str) -> Path:
        c[name] = dir_ / name
        return c[name]

    h264 = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    _ff(*_src(4), *h264, "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(p("good_h264.mp4")))
    if "libvpx-vp9" in enc:
        _ff(*_src(3), "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "300k",
            "-c:a", "libopus", "-shortest", str(p("good_vp9.webm")))  # fmt: skip
    if "libx265" in enc:
        _ff(*_src(3), "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
            "-c:a", "aac", "-shortest", str(p("good_hevc.mkv")))  # fmt: skip
    _ff(*_src(3, audio=False), *h264, str(p("noaudio.mp4")))
    _ff(*_src(4), *h264, "-c:a", "aac", "-shortest",
        "-vf", "setpts='if(lt(N,40),PTS,PTS*2)'", "-fps_mode", "vfr", str(p("vfr.mkv")))  # fmt: skip
    if _supports_display_rotation():
        _ff("-display_rotation", "90", "-i", str(c["good_h264.mp4"]), "-c", "copy", str(p("rotated.mp4")))

    # ---------- bad inputs ----------
    _ff(*[x.format(d=3) for x in A], "-c:a", "aac", "-f", "mp4", str(p("audio_only.mp4")))
    _ff("-f", "lavfi", "-i", "testsrc2=s=320x240", "-frames:v", "1", "-f", "image2", "-c:v", "png",
        str(p("image.mp4")))  # fmt: skip
    p("text.mp4").write_text("this is not a video\n" * 200)
    p("random.mp4").write_bytes(os.urandom(200_000))
    # moov at the end + cut in half -> header missing
    _ff(*_src(4), *h264, "-c:a", "aac", "-shortest", str(dir_ / "_full_tail_moov.mp4"))
    data = (dir_ / "_full_tail_moov.mp4").read_bytes()
    p("truncated_no_moov.mp4").write_bytes(data[: len(data) // 2])
    # moov at the start (faststart) + cut -> header claims 20 s, data stops early
    _ff(*_src(20), *h264, "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(dir_ / "_full_fast.mp4"))
    data = (dir_ / "_full_fast.mp4").read_bytes()
    p("truncated_tail.mp4").write_bytes(data[: int(len(data) * 0.4)])
    p("playlist.mp4").write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nfile:///etc/passwd\n#EXT-X-ENDLIST\n"
    )
    _ff(*_src(2, audio=False), "-c:v", "qtrle", str(p("qtrle.mov")))
    _ff(*_src(0.5), *h264, "-c:a", "aac", "-shortest", str(p("too_short.mp4")))
    _ff("-f", "lavfi", "-i", "testsrc2=s=32x32:r=30:d=2", *h264, str(p("tiny.mp4")))
    return c
