# Configuration

Clipper reads environment variables prefixed `CLIPPER_`, or a `.env` file in the working directory (copy `.env.example`).
Lists use JSON syntax, e.g. `CLIPPER_ALLOWED_HOSTS=["127.0.0.1","localhost"]`.
A test (`backend/tests/test_config.py`) fails if a setting exists that is not documented here and in `.env.example`.

| Variable | Default | Meaning |
|---|---|---|
| `CLIPPER_DATA_DIR` | `data` | Folder for the SQLite DB, imported media, artifacts and exports. Put it on a drive with plenty of free space (10 GB+ recommended). |
| `CLIPPER_DATABASE_URL` | *(empty → SQLite in data dir)* | SQLAlchemy URL. PostgreSQL (`postgresql+psycopg://…`, install extra `clipper[postgres]`) is **experimental and untested until v1.0**. |
| `CLIPPER_HOST` | `127.0.0.1` | Bind address. There is no login before v1.0 — do not expose to a network without an authenticating reverse proxy. |
| `CLIPPER_PORT` | `8765` | HTTP port. |
| `CLIPPER_ALLOWED_HOSTS` | `["127.0.0.1","localhost"]` | Accepted `Host` header values (blocks DNS-rebinding attacks). Add your proxy's host name if you use one. |
| `CLIPPER_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `CLIPPER_LOG_JSON` | `true` | JSON log lines on stderr; `false` = human-readable. Logs contain IDs, never transcripts or file names. |
| `CLIPPER_JOB_LEASE_SECONDS` | `60` | How long a worker owns a job between heartbeats. After a crash, the job is retried once the lease expires. |
| `CLIPPER_JOB_MAX_ATTEMPTS` | `3` | Attempts per job (crashes and transient errors count). |
| `CLIPPER_JOB_RETRY_BACKOFF_SECONDS` | `[30,120,600]` | Delay before retry *n* (the last value repeats). |
| `CLIPPER_WORKER_POLL_SECONDS` | `1.0` | Idle polling interval of workers. |
| `CLIPPER_WORKER_CONCURRENCY` | `1` | Worker threads started by `clipper serve` / `clipper worker`. Video jobs are CPU/GPU-heavy; raise carefully. |
| `CLIPPER_MAX_UPLOAD_BYTES` | `21474836480` (20 GiB) | Largest accepted upload *(enforced from M2)*. |
| `CLIPPER_MAX_DURATION_SECONDS` | `14400` (4 h) | Longest accepted video *(enforced from M2)*. |
| `CLIPPER_TEMP_RETENTION_HOURS` | `24` | Age after which temporary files are swept *(from M2)*. |
