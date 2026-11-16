# Changelog
All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/).

## [Unreleased]
### Added
- M1 foundation: settings, structured logging, safe local storage, SQLite + Alembic migrations,
  database-backed job queue (leases, fencing, retry/backoff, cancellation, crash recovery),
  REST API (`/api/v1/projects`, `/api/v1/jobs`, `/healthz`, `/readyz`), CLI (`serve`, `worker`, `migrate`, `doctor`),
  CI workflow, tests.
- M0 prototypes in `spikes/` and results in `docs/benchmarks.md`.
