# Changelog
All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Added
- M6 (in progress) voiceover: record commentary in the editor while the clip plays (or upload a recording), place
  takes, set video/voice levels and ducking; exports level voice against the original, duck the original while
  you speak and normalise the mix. Preview plays takes in sync with the same ducking curve. Manifest lists takes and
  voice coverage. `POST/GET /clips/{id}/voice`, `GET /voice/{id}/audio`, `DELETE /voice/{id}`.
- M6 (in progress) text overlays: chapter titles, callouts and labels with start/end times, dragged into place in
  the preview and burned into exports above the captions. Server-side layout (`POST /overlays/layout`,
  `GET /overlay-styles`) keeps preview and export identical; listed in the export manifest.
- M5 render & export: `POST /clips/{id}/exports` renders the saved clip to a 9:16 MP4 (H.264/AAC, burned-in
  captions via libass, crop or blurred-background fit) in three qualities, plus SRT/VTT caption files and a
  `clip.json` manifest. Settings are frozen per export (with a hash; duplicate requests reuse the running export).
  Loudness targets −14 LUFS / −1.5 dBTP with capped limiting and an honest note when a clip stays quieter. Every
  file is verified (codec, size, duration ±1 frame, audio, loudness) before it is marked done. Progress, cancel,
  delete, download with a readable file name, and export history on the project page.
- Bundled OFL fonts (Montserrat, Anton) used by both the preview and the renderer; preview caption layout now
  matches the export (tested within 3 px on a 1080×1920 frame).
- `scripts/bench_render.py`; Playwright export/download test and a full speech flow test (OS text-to-speech →
  transcription → edit → export); CI runs the Playwright suite on Linux, Windows and macOS.
- M4 web editor (`frontend/`, served by `clipper serve`): projects, resumable import with rights confirmation,
  analysis with progress/cancel, clip gallery (approve/reject/delete, manual clips), and a clip editor with
  waveform trimming (drag, keyboard, snap to words, loop), transcript word corrections, four caption styles with
  position, 9:16 crop/fit framing with drag-to-pan, and a live preview. Edits save with optimistic concurrency and
  a "load the latest version" path on conflict. API: clip `edit` object, `GET /caption-presets`,
  `GET /clips/{id}/captions`, versioned `PATCH /assets/{id}/transcript`, `discard_edits` on analyze.
  Tests: Vitest unit tests, Playwright flows against a real server (installed Edge/Chrome), shared framing vectors.
### Security
- `/api/*` now requires a per-install access token (`data/api_token`; header `X-Clipper-Token` or the
  `HttpOnly`/`SameSite=Strict` cookie set by the UI). Scripts calling the API must send the header.
- UI pages are served with a strict Content-Security-Policy (no inline scripts or styles, no framing).
### Added (earlier milestones)
- M3 AI analysis: `ai.analyze` job with local faster-whisper transcription (GPU with CPU fallback, model cache
  in `data/models`), FFmpeg scene-cut detection and an explainable highlight scorer (speech mode on sentence
  boundaries; audio mode on loudness and pace for videos with little speech; English-only keyword features
  switched off for other languages; never pads results with poor moments). New endpoints: analyze, transcript,
  clips list/get/create/patch (If-Match versioning)/delete. Settings `CLIPPER_ASR_MODEL`, `CLIPPER_ASR_DEVICE`,
  `CLIPPER_ASR_CPU_THREADS`. Opt-in real-ASR test (`CLIPPER_TEST_ASR=1`), `scripts/smoke_analyze.py`,
  `scripts/bench_server_memory.py`.
- M2 media import: resumable chunked upload with rights declaration, disk-space and size limits;
  `media.import` job (sha256, ffprobe validation with demuxer/protocol allow-lists, head/tail decode check,
  one-pass 540p proxy + thumbnail sprite + 16 kHz ASR audio, waveform); asset endpoints for proxy (HTTP Range),
  thumbnails, waveform and delete; hourly sweeper for abandoned uploads and temp files; `doctor` checks FFmpeg
  features; generated bad-input test corpus; long-file benchmark script (`scripts/bench_long_import.py`).
### Fixed
- Model downloads from Hugging Face could hang forever at 0 bytes through the `hf_xet` transfer backend; Clipper
  now uses plain HTTPS by default (`HF_HUB_DISABLE_XET=0` re-enables Xet).
- Byte counters use 64-bit integers (would overflow above 2 GB on PostgreSQL).
- M1 foundation: settings, structured logging, safe local storage, SQLite + Alembic migrations,
  database-backed job queue (leases, fencing, retry/backoff, cancellation, crash recovery),
  REST API (`/api/v1/projects`, `/api/v1/jobs`, `/healthz`, `/readyz`), CLI (`serve`, `worker`, `migrate`, `doctor`),
  CI workflow, tests.
- M0 prototypes in `spikes/` and results in `docs/benchmarks.md`.
