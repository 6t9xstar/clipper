"""Voice tracks: the user's own commentary, recorded in the browser (MediaRecorder: WebM/Ogg Opus,
MP4/AAC) or uploaded as an audio file. Each upload is converted once to 48 kHz mono FLAC and analysed:
duration, integrated loudness (for levelling against the video's sound) and the stretches where someone is
speaking (for ducking).
"""

from __future__ import annotations

import array
import math
import sys
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from clipper.jobs import proc
from clipper.media import prepare
from clipper.media.probe import tool
from clipper.render import graph

VOICE_DEMUXERS = "matroska,webm,ogg,wav,mp3,flac,mov,aac"  # what browsers record and common audio files
VOICE_INPUT_ARGS = ["-protocol_whitelist", "file", "-format_whitelist", VOICE_DEMUXERS]
MAX_SECONDS = 600.0
MIN_SECONDS = 0.3
MAX_BYTES = 200 * 1024**2
FRAME = 0.02  # s per RMS frame for speech detection
ANALYSIS_RATE = 8000
FLOOR_DB = -50.0  # never treat anything quieter than this as speech
BELOW_PEAK_DB = 28.0  # speech = within this many dB of the take's loud parts
HANGOVER = 0.25  # s: gaps shorter than this inside speech are bridged
MIN_SPEECH = 0.1  # s: shorter blips are ignored (clicks, breaths)


class VoiceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def convert_cmd(src: Path, flac: Path) -> list[str]:
    return [
        tool("ffmpeg"), "-hide_banner", "-v", "error", "-nostdin", "-y", *VOICE_INPUT_ARGS, "-i", str(src),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "48000", "-t", f"{MAX_SECONDS:.0f}",
        "-c:a", "flac", "-f", "flac", str(flac),
    ]  # fmt: skip


def analysis_wav_cmd(flac: Path, wav: Path) -> list[str]:
    return [tool("ffmpeg"), "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(flac),
            "-ac", "1", "-ar", str(ANALYSIS_RATE), "-c:a", "pcm_s16le", "-f", "wav", str(wav)]  # fmt: skip


def rms_frames(wav_path: Path) -> list[float]:
    """RMS level in dBFS per FRAME seconds."""
    out: list[float] = []
    with wave.open(str(wav_path), "rb") as w:
        per = round(w.getframerate() * FRAME)
        while True:
            raw = w.readframes(per * 500)
            if not raw:
                break
            a = array.array("h")
            a.frombytes(raw)
            if sys.byteorder == "big":
                a.byteswap()
            for i in range(0, len(a), per):
                seg = a[i : i + per]
                ms = math.sumprod(seg, seg) / len(seg)
                out.append(10 * math.log10(ms / 32768**2) if ms > 0 else -120.0)
    return out


def speech_segments(levels: list[float]) -> list[list[float]]:
    """Frames loud enough to be speech -> [[start, end], ...] in seconds (bridged, short blips dropped)."""
    if not levels:
        return []
    loud = sorted(levels)[int(0.95 * (len(levels) - 1))]
    thr = max(FLOOR_DB, loud - BELOW_PEAK_DB)
    segs: list[list[float]] = []
    for i, db in enumerate(levels):
        if db < thr:
            continue
        a, b = i * FRAME, (i + 1) * FRAME
        if segs and a - segs[-1][1] <= HANGOVER:
            segs[-1][1] = b
        else:
            segs.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in segs if b - a >= MIN_SPEECH]


def prepare_track(upload: Path, flac: Path, should_cancel: Callable[[], bool]) -> dict[str, Any]:
    """upload -> flac next to it; returns duration_ms, loudness_lufs, segments, peaks. Raises VoiceError."""
    r = proc.run(convert_cmd(upload, flac), should_cancel=should_cancel, timeout=300)
    if r.returncode != 0 or not flac.exists() or flac.stat().st_size == 0:
        raise VoiceError("VOICE_UNREADABLE", "This file has no audio Clipper can read.")
    wav = flac.with_suffix(".analysis.wav")
    try:
        r = proc.run(analysis_wav_cmd(flac, wav), should_cancel=should_cancel, timeout=300)
        if r.returncode != 0:
            raise VoiceError("VOICE_UNREADABLE", "This file has no audio Clipper can read.")
        with wave.open(str(wav), "rb") as w:
            duration = w.getnframes() / w.getframerate()
        if duration < MIN_SECONDS:
            raise VoiceError("VOICE_TOO_SHORT", "The recording is shorter than 0.3 seconds.")
        levels = rms_frames(wav)
        peaks = prepare.waveform(wav, should_cancel=should_cancel)
    finally:
        wav.unlink(missing_ok=True)
    r = proc.run(graph.ebur128_cmd(flac), should_cancel=should_cancel, timeout=300)
    lufs = graph.parse_ebur128(r.stderr.decode("utf-8", "replace"))
    return {
        "duration_ms": round(duration * 1000),
        "loudness_lufs": lufs,  # None: silent
        "segments": speech_segments(levels),
        "peaks": peaks,
    }
