# Changelog
All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Added
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
