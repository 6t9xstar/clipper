"""Speech recognition with faster-whisper (MIT; Whisper weights MIT). Runs fully locally.

Device selection (CLIPPER_ASR_DEVICE=auto): NVIDIA GPU if CTranslate2 sees one AND the model loads (CUDA/cuDNN
libraries present), otherwise CPU. The reason for any fallback is recorded with the transcript.
Measured on M0 (docs/benchmarks.md): CPU `small` int8 ~0.16x realtime, GPU `large-v3-turbo` ~0.05x realtime.
"""

from __future__ import annotations

import os
import site
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clipper.core.logging import log

# Model files come from Hugging Face. Its Xet transfer backend (hf_xet) stalled indefinitely at 0 bytes during
# M3 testing, while plain HTTPS fetched the same 75 MB file in ~6 s. Use HTTPS unless the user opts back in.
# Must be set before huggingface_hub is imported (it reads the variable at import time).
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

GPU_DEFAULT, CPU_DEFAULT = "large-v3-turbo", "small"
KNOWN_MODELS = {
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "medium.en",
    "large-v2",
    "large-v3",
    "large-v3-turbo",
    "distil-large-v3",
}
_dll_done = False
_cache_lock = threading.Lock()
_cache: dict[tuple[str, str, str], Any] = {}


def _register_cuda_dlls() -> None:
    """pip-installed NVIDIA wheels put DLLs in site-packages/nvidia/*/bin, which Windows does not search."""
    global _dll_done
    if _dll_done or os.name != "nt":
        return
    for sp in site.getsitepackages():
        for b in Path(sp, "nvidia").glob("*/bin"):
            os.add_dll_directory(str(b))
            os.environ["PATH"] = f"{b}{os.pathsep}{os.environ.get('PATH', '')}"
    _dll_done = True


def cuda_available() -> bool:
    _register_cuda_dlls()
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        return False


@dataclass
class Word:
    w: str
    s: float
    e: float
    p: float


@dataclass
class TranscriptResult:
    language: str
    language_probability: float
    duration: float
    model: str
    device: str
    words: list[Word] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "language_probability": round(self.language_probability, 3),
            "duration": self.duration,
            "model": self.model,
            "device": self.device,
            "notes": self.notes,
            "words": [
                {"w": x.w, "s": round(x.s, 3), "e": round(x.e, 3), "p": round(x.p, 3)} for x in self.words
            ],
        }


def _load(model: str, device: str, models_dir: Path, cpu_threads: int) -> Any:
    from faster_whisper import WhisperModel

    compute = "int8_float16" if device == "cuda" else "int8"
    key = (model, device, compute)
    with _cache_lock:
        if key not in _cache:
            _cache.clear()  # keep one model resident (GPU memory is scarce)
            t0 = time.time()
            _cache[key] = WhisperModel(
                model,
                device=device,
                compute_type=compute,
                download_root=str(models_dir),
                cpu_threads=cpu_threads,
            )
            log.info("asr.model_loaded", model=model, device=device, seconds=round(time.time() - t0, 1))
        return _cache[key]


def resolve(model_setting: str, device_setting: str) -> tuple[str, str, list[str]]:
    notes: list[str] = []
    device = device_setting
    if device == "auto":
        device = "cuda" if cuda_available() else "cpu"
    elif device == "cuda" and not cuda_available():
        notes.append("CUDA requested but no usable NVIDIA GPU found; using CPU")
        device = "cpu"
    model = model_setting if model_setting != "auto" else (GPU_DEFAULT if device == "cuda" else CPU_DEFAULT)
    if model not in KNOWN_MODELS:
        raise ValueError(f"unknown ASR model {model!r}")
    return model, device, notes


def transcribe(
    audio: Path,
    *,
    model_setting: str,
    device_setting: str,
    models_dir: Path,
    cpu_threads: int = 0,
    language: str | None = None,
    on_progress: Callable[[float], None],
    should_cancel: Callable[[], bool],
) -> TranscriptResult:
    """Word-level transcript. Raises JobCancelled if cancelled between segments."""
    from clipper.jobs.errors import JobCancelled

    model, device, notes = resolve(model_setting, device_setting)
    models_dir.mkdir(parents=True, exist_ok=True)
    try:
        wm = _load(model, device, models_dir, cpu_threads)
    except Exception as e:
        if device != "cuda":
            raise
        notes.append(f"GPU model load failed ({type(e).__name__}); fell back to CPU")
        log.warning("asr.gpu_fallback", error=str(e)[:200])
        device = "cpu"
        model = model_setting if model_setting != "auto" else CPU_DEFAULT
        wm = _load(model, device, models_dir, cpu_threads)

    segments, info = wm.transcribe(
        str(audio),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,  # avoids runaway repetition on long files
        beam_size=5,
    )
    res = TranscriptResult(
        language=info.language,
        language_probability=float(info.language_probability or 0),
        duration=float(info.duration),
        model=model,
        device=device,
        notes=notes,
    )
    total = max(1e-6, float(info.duration))
    for seg in segments:  # generator: decoding happens while we iterate -> cancellable
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                res.words.append(Word(text, float(w.start), float(w.end), float(w.probability)))
        on_progress(min(1.0, float(seg.end) / total))
        if should_cancel():
            raise JobCancelled()
    return res
