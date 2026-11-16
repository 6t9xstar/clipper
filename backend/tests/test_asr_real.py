"""Real speech recognition with faster-whisper `tiny` on locally synthesised speech.

Opt-in because it downloads the model (~75 MB) from Hugging Face on first run:
    CLIPPER_TEST_ASR=1 [CLIPPER_TEST_MODELS_DIR=data/models] uv run pytest backend/tests/test_asr_real.py
Speech is generated with the OS text-to-speech engine (Windows SAPI, macOS `say`, or espeak-ng); the test is
skipped where none is available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from clipper.ai import asr

TEXT = (
    "Why do most new runners get injured in the first three weeks? "
    "They increase their distance far too quickly. Start slowly and your knees will thank you."
)
EXPECT = {"runners", "injured", "weeks", "distance", "knees"}

pytestmark = pytest.mark.skipif(os.environ.get("CLIPPER_TEST_ASR") != "1", reason="set CLIPPER_TEST_ASR=1")


def _speech(out: Path) -> None:
    raw = out.with_suffix(".raw.wav")
    if sys.platform == "win32":
        quoted = TEXT.replace("'", "''")  # PowerShell single-quote escaping
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = 0; $s.SetOutputToWaveFile('{raw}'); $s.Speak('{quoted}'); $s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=120)
    elif shutil.which("say"):
        subprocess.run(["say", "-o", str(raw), "--data-format=LEI16@22050", TEXT], check=True, timeout=120)
    elif shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-w", str(raw), TEXT], check=True, timeout=120)
    else:
        pytest.skip("no text-to-speech engine available")
    ff = shutil.which("ffmpeg")
    if ff is None:
        pytest.skip("ffmpeg not installed")
    subprocess.run([ff, "-v", "error", "-y", "-i", str(raw), "-ac", "1", "-ar", "16000", str(out)],
                   check=True, timeout=120)  # fmt: skip


def test_tiny_model_transcribes_speech(tmp_path: Path) -> None:
    wav = tmp_path / "speech.wav"
    _speech(wav)
    models = Path(os.environ.get("CLIPPER_TEST_MODELS_DIR", tmp_path / "models")).resolve()
    progress: list[float] = []
    res = asr.transcribe(wav, model_setting="tiny", device_setting="cpu", models_dir=models,
                         on_progress=progress.append, should_cancel=lambda: False)  # fmt: skip
    assert res.language == "en" and res.model == "tiny" and res.device == "cpu"
    got = {w.w.lower().strip(".,?!") for w in res.words}
    assert len(EXPECT & got) >= 4, got  # tiny is not perfect; allow one miss
    assert all(w.s <= w.e for w in res.words)
    assert all(a.s <= b.s for a, b in zip(res.words, res.words[1:], strict=False))
    assert res.words[-1].e <= res.duration + 0.5
    assert progress and progress[-1] == pytest.approx(1.0, abs=0.15)


def test_cancel_stops_transcription(tmp_path: Path) -> None:
    from clipper.jobs.errors import JobCancelled

    wav = tmp_path / "speech.wav"
    _speech(wav)
    models = Path(os.environ.get("CLIPPER_TEST_MODELS_DIR", tmp_path / "models")).resolve()
    with pytest.raises(JobCancelled):
        asr.transcribe(wav, model_setting="tiny", device_setting="cpu", models_dir=models,
                       on_progress=lambda f: None, should_cancel=lambda: True)  # fmt: skip
