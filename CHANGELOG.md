# Changelog
All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Added
- M2 media import: resumable chunked upload with rights declaration, disk-space and size limits;
  `media.import` job (sha256, ffprobe validation with demuxer/protocol allow-lists, head/tail decode check,
  one-pass 540p proxy + thumbnail sprite + 16 kHz ASR audio, waveform); asset endpoints for proxy (HTTP Range),
  thumbnails, waveform and delete; hourly sweeper for abandoned uploads and temp files; `doctor` checks FFmpeg
  features; generated bad-input test corpus; long-file benchmark script (`scripts/bench_long_import.py`).
### Fixed
- Byte counters use 64-bit integers (would overflow above 2 GB on PostgreSQL).
- M1 foundation: settings, structured logging, safe local storage, SQLite + Alembic migrations,
  database-backed job queue (leases, fencing, retry/backoff, cancellation, crash recovery),
  REST API (`/api/v1/projects`, `/api/v1/jobs`, `/healthz`, `/readyz`), CLI (`serve`, `worker`, `migrate`, `doctor`),
  CI workflow, tests.
- M0 prototypes in `spikes/` and results in `docs/benchmarks.md`.
