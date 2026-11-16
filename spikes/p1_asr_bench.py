"""P1: faster-whisper speed/accuracy benchmark. Usage: python p1_asr_bench.py <device> <model> [compute]"""
import json, os, re, subprocess, sys, time
from pathlib import Path

if os.name == "nt":  # pip-installed CUDA wheels aren't on the DLL search path on Windows
    import site
    for sp in site.getsitepackages():
        for b in Path(sp, "nvidia").glob("*/bin"):
            os.add_dll_directory(str(b)); os.environ["PATH"] = f"{b};{os.environ['PATH']}"

from faster_whisper import WhisperModel

M = Path(__file__).parent / "media"
LONG = M / "speech_x7.wav"
if not LONG.exists():  # ~10 min: loop the 87 s sample 7 times
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "6", "-i", str(M / "speech.wav"), "-c", "copy", str(LONG)], check=True)


def norm(t: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", t.lower()).split()


def wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1] / len(ref)


device, model = sys.argv[1], sys.argv[2]
compute = sys.argv[3] if len(sys.argv) > 3 else ("int8" if device == "cpu" else "float16")
t0 = time.perf_counter()
wm = WhisperModel(model, device=device, compute_type=compute, cpu_threads=8 if device == "cpu" else 0)
load = time.perf_counter() - t0
t0 = time.perf_counter()
segs, info = wm.transcribe(str(LONG), language="en", word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
words = [w for s in segs for w in s.words]
dt = time.perf_counter() - t0
ref = norm((M / "reference.txt").read_text(encoding="utf-8-sig")) * 7
hyp = norm(" ".join(w.word for w in words))
print(json.dumps({"device": device, "model": model, "compute": compute, "audio_s": round(info.duration, 1),
                  "load_s": round(load, 1), "transcribe_s": round(dt, 1), "rtf": round(dt / info.duration, 3),
                  "wer": round(wer(ref, hyp), 3), "words": len(words)}))
