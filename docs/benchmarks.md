# M0 Prototype Results
**Date:** 2026-11-15 · **Machine:** Windows 11 Pro, 20 logical CPUs, NVIDIA RTX A1000 Laptop GPU 4 GB (driver 597.06), FFmpeg 8.1.2 (gyan.dev full build), Python 3.12 via uv 0.12.17.
**Code:** `spikes/` (throwaway; not product code). Re-run with `cd spikes && uv sync && uv run python <script>`; media via `powershell -File make_media.ps1`.
**Test media:** synthetic only — Windows SAPI TTS speech (87 s, 9 paragraphs, known reference text) over FFmpeg lavfi test patterns. No third-party media used.

## Summary
| Spike | Result | Decision |
|---|---|---|
| P1a ASR speed | ✅ CPU `small` int8 = 0.16× realtime; GPU `large-v3-turbo` = 0.05× | Default CPU `small`/`base`; GPU `large-v3-turbo` int8_float16 (fits 4 GB) |
| P1b Queue | ✅ 150/150 jobs after hard worker kill, 0 double completions; FFmpeg tree cancel 0.31 s | Custom SQLite lease queue. **Huey rejected** (SQLite storage deletes task on dequeue → lost on crash, `huey/storage.py:948`) |
| P2 Dynamic crop | ✅ 60 s 1080p → 1080×1920 moving crop: sendcmd 4.8× realtime, PyAV pipe 2.25× | FFmpeg `sendcmd` as primary renderer |
| P3 Framing smoother | ✅ (simulated detections) 98.5–99.3% subject-in-safe-zone, camera static 81–86% of frames, 0 false cuts | One-Euro + dead-zone + speed limit + confirmed hard cuts. **Active-speaker: not tested — no-go for automation until real footage** |
| P4 Highlight scorer | ⚠️ precision@4 = 0.75 (random ≈ 0.44); 4th pick is the right content minus lead-in | Mechanism validated; **quality unproven** (author-labelled 9-paragraph sample) |
| P5 Captions | ✅ libass: centre ±1 px, word onset 0–17 ms (≤1 frame). ⚠️ Browser renders same font size ~11% larger | Preview must scale CSS size by font metrics; per-font golden test |

## P1a — faster-whisper 1.2.1 (608 s audio, word timestamps, VAD on)
| Device | Model | Compute | Load s | Transcribe s | RTF | WER |
|---|---|---|---|---|---|---|
| CPU (8 thr) | tiny | int8 | 1.5 | 23.3 | 0.038 | 1.4% |
| CPU | base | int8 | 1.6 | 34.2 | 0.056 | 1.6% |
| CPU | small | int8 | 2.4 | 99.1 | 0.163 | 1.3% |
| CUDA | small | float16 | 2.4 | 36.4 | 0.060 | 1.2% |
| CUDA | large-v3-turbo | float16 | 5.2 | 31.2 | 0.051 | 1.8% |
| CUDA | large-v3-turbo | int8_float16 | 7.3 | 28.1 | 0.046 | 1.6% |

Extrapolation (assumption): 60-min video ≈ 10 min ASR on CPU `small`, ≈ 3 min on this GPU. WER is **not meaningful for model choice** — clean TTS; needs real speech (accents, music, crosstalk).
GPU VRAM used ≈ 1.2 GB for large-v3-turbo.

## Problems found (must be handled in M1)
1. **faster-whisper 1.2.1 breaks with PyAV ≥ 18** (`av.open(..., metadata_errors=...)` removed). Pin `av<18` (17.1.0 verified).
2. **CUDA on Windows:** ctranslate2 4.9 needs `cublas64_12.dll` / cuDNN 9; not present by default. Fix verified: pip `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` and register `site-packages/nvidia/*/bin` with `os.add_dll_directory`. Must be an optional `[gpu]` extra + `clipper doctor` check.
3. **mediapipe 1.2.3** removed legacy `solutions` API (Tasks API only, requires separate `.tflite` model) and pulls `opencv-python` + `opencv-contrib-python` 5.0 alongside headless → duplicate OpenCV. Decision for M1: evaluate **OpenCV YuNet only** (single dependency, Apache-2.0) and drop mediapipe if accuracy is acceptable.
4. **Caption width:** a 5-word line at 80 px spans 96% of a 1080 px frame → automatic line-breaking / max-chars per line needed.
5. **libass vs CSS font size:** libass scales font so ascent+descent = size; CSS uses em. Ratio for Arial ≈ 0.87–0.90 measured. Preview must apply `cssSize = assSize × unitsPerEm / (ascender + |descender|)` from font tables.
6. Plan update: FFmpeg 8.1 is current on Windows (plan said 7.x).

## Not yet verified (needs real media)
- Face detection accuracy (YuNet/MediaPipe) on real faces, profile views, low light.
- Active-speaker matching for 2-person podcasts.
- ASR accuracy on real speech, gaming audio with music/SFX.
- Highlight usefulness on real long-form content (needs a labelled set of real videos).
- Performance on a minimum-spec machine (4 cores, 8 GB, no GPU).
