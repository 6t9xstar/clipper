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

---

# M2 — Long-file import (2026-11-16)
`uv run python scripts/bench_long_import.py --hours 3 --data-dir data/bench` (same machine as above).
Synthetic 3 h source: 640×360 30 fps H.264 + AAC tone, 1138 MB.

| Metric | Result |
|---|---|
| Status | `ready`, duration 10 800 000 ms |
| Upload (64 MiB chunks, in-process client) | 10.1 s |
| Import (hash, probe, decode check, proxy + sprite + ASR audio, waveform) | 1883 s = **5.7× faster than real time** |
| Peak FFmpeg RSS | 220 MB |
| Peak Python RSS (client **and** server in one process) | 473 MB — not a server-only figure; see below |
| Thumbnails | 120 tiles (cap reached), every 90 s |

Notes:
- The source is 360p, so the proxy is not downscaled; 1080p sources will import slower (decode-bound). To be measured
  with real footage.
- The in-process benchmark cannot separate the test client's buffering of 64 MiB chunks from the server; see the
  separate-process measurement below.

### Server-only memory (`scripts/bench_server_memory.py`, same 3 h file, real `clipper serve` process)
| Phase | Peak RSS of the whole server process tree (Python + FFmpeg) |
|---|---|
| Upload (64 MiB chunks over HTTP) | **96 MB** |
| Import | **317 MB** |

Upload 18.4 s. The import wall time of this run is **not valid**: the PC went into sleep/hibernation mid-run
(Windows event 42, "Standby Battery Budget Exceeded"); the earlier 1883 s figure stands. The run's Python-only
figure (5 MB) was wrong — it measured the Windows venv launcher, not the interpreter; the script is fixed but
has not been re-run, so no Python-only figure is reported.

# M3 — AI analysis (2026-11-16)

Same machine. All numbers below are from real runs; nothing is estimated.

| Check | Result |
|---|---|
| `scripts/smoke_analyze.py` — real `clipper serve` process, 63 s talk video (Windows SAPI text-to-speech over generated visuals, 2 strong + 3 filler paragraphs), real faster-whisper `tiny` | Model ran on **CUDA**; 142 words; analysis job **10.2 s** end to end (incl. model load). Suggested exactly the 2 strong paragraphs, each whole (10.03–27.01 s, 38.15–51.13 s); note "only 2 of 3 requested moments scored well enough" |
| `test_asr_real.py` (`CLIPPER_TEST_ASR=1`, `tiny`, CPU) | 2 passed: expected words found, timestamps ordered, cancellation stops decoding |
| Scene cuts (`scdet`, threshold 12, proxy at 10 fps/160 px) | Exact on synthetic hard cuts (±0.1 s). **Missed** a red→green cut: both colours have the same luma (≈81), and scdet did not react to the chroma change. Real cuts nearly always change brightness, but this is a known blind spot |
| Hugging Face model download | Hung at 0 bytes for >10 min through `hf_xet`; plain HTTPS fetched the same 75 MB in 5.7 s. Fixed by defaulting to HTTPS |

Scorer issues found by the smoke test on a real transcript and fixed (with regression tests):
1. A clip could stop mid-paragraph: "ends on a complete point" only checked for a trailing question mark, so a cut-off
   clip tied with the whole paragraph and won as the shorter one. Now it requires a ≥ 1 s pause afterwards.
2. The requested count was filled with negative-score (filler) moments. Now only positive scores are suggested.
3. Audio mode normalised loudness to the 95th percentile, so a short loud burst in a long calm video scored zero;
   it also preferred windows that cut the burst off. Now: 99th percentile and a "cut off mid-action" penalty.

Not yet measured: transcription speed of the in-app job on a long real podcast (M0 figures above remain the
reference: CPU `small` ≈ 0.16× real time, GPU `large-v3-turbo` ≈ 0.05× real time); suggestion quality on real
podcasts/gameplay with human ratings (planned before v0.1 with user-supplied, permitted footage).

# M5 — Render & export (2026-11-16)
Machine: Intel i7-12800H (14 cores), 32 GB RAM, Windows 11, FFmpeg 8.1, CPU encoding (libx264). Source generated
by `scripts/bench_render.py`: 1920×1080 30 fps moving test pattern with temporal film-grain noise (so it is not
unrealistically easy to compress) and a tone. 45 s clip, 9:16 crop, burned-in "bold" captions every 0.35 s.
No third-party media.

| Grain | Quality | Render | Speed | Output | Loudness |
|---|---|---|---|---|---|
| 6 (≈ camera footage) | standard (1080×1920, crf 20, veryfast) | 18.1 s | 2.48× real time | 49.9 MB | −13.9 LUFS |
| 6 | high (crf 17, medium) | 66.9 s | 0.67× | 122.1 MB | |
| 18 (heavy grain) | standard | 32.2 s | 1.40× | 91.8 MB | |
| 18 | high | 90.6 s | 0.50× | 138.5 MB | |

Draft (720×1280, ultrafast) at grain 6: 15.0 s (2.99×); "fit" framing (blurred background) standard: 19.6 s
(2.29×) — both measured before the bitrate caps were added. The loudness pass adds ≈ 0.6 s and verification
≈ 0.2 s per 45 s clip. Before the bitrate caps, heavy grain at `standard` produced 330 MB for 45 s; the caps
(16 / 24 / 6 Mbit/s) bound the size at a small quality cost on very noisy footage.

Reproduce: `uv run python scripts/bench_render.py --seconds 45 --quality standard --grain 6 --workdir data`.

**Preview ↔ export caption parity (plan P5, AC ≤ 2 % of frame):** for every preset, `test_render.py` renders a
caption with FFmpeg/libass and compares the rows of actual glyph ink with the positions the browser maths
predicts: within 3 px of 1920 (0.16 %). Checked once in a real browser as well: the burned-in "THREE WEEKS?" line
spans rows 1141–1237 and columns 238–839; the preview draws it at 1141.6–1237.8 and 238–842.

**Loudness on real speech:** OS text-to-speech (Windows SAPI) audio is quiet with sharp peaks; the first version
(loudnorm only) left it at −17 LUFS and failed verification. Gain alone cannot reach −14 LUFS without exceeding
the true-peak limit, so the export now applies at most 8 dB of limiting: it lands at −16.3 LUFS within the
true-peak limit and reports that as a loudness note instead of over-compressing.

Not yet measured: hardware encoders (not implemented), macOS/Linux render speeds, long (10 min) clips, real
camera footage.
