# Architecture — as implemented (M2)

Target architecture: [PHASE1_PLAN.md §6](PHASE1_PLAN.md). This page describes only what exists in code today.

```
clipper serve ──► FastAPI app (backend/clipper/api)          clipper worker ──► Worker loop(s)
                    │  LocalGuardMiddleware (Host/Origin)                        │
                    │  problem+json errors                                       │ claim / heartbeat / finish
                    ▼                                                            ▼
               SQLite (WAL) ◄──────────── jobs table = queue ─────────────► JobQueue (jobs/queue.py)
                    ▲                                                            │
               LocalStorage (data/storage, safe keys, atomic writes)       proc.run(): ffmpeg etc,
                                                                            process-tree kill on cancel
```

## Job lifecycle
`queued → running → succeeded | failed | cancelled`

| Event | What happens |
|---|---|
| Claim | One atomic `UPDATE … WHERE id=(SELECT … LIMIT 1) RETURNING`; issues a new `lease_token`; `attempts += 1` |
| Heartbeat | Every lease/3 s by a background thread; extends the lease, flushes progress, reads `cancel_requested` |
| Success / failure | Writes are **fenced** on `lease_token`: a worker that lost its lease cannot overwrite results |
| `RetryableJobError` / unknown exception | Back to `queued` with backoff until `max_attempts`, then `failed` |
| `JobError` | `failed` immediately with its stable `code` |
| Worker crash | Lease expires → next claim picks the job up again; on the final attempt → `failed / WORKER_LOST` |
| Cancel | Queued: immediate. Running: flag → handler's next `check_cancel()`/`progress()` raises; running subprocesses are killed as a tree |

Verified by `backend/tests/test_queue.py` and `test_recovery.py` (real worker process killed mid-job).

## Security measures in place
- Bind to 127.0.0.1 by default; `Host` allow-list (DNS rebinding) and `Origin` check on writes (CSRF).
- Storage keys: server-generated, segment whitelist, Windows reserved names rejected, `resolve()` + root containment.
- IDs validated before touching DB or disk; external tools run with argv lists (no shell).
- Logs carry IDs only.

## Media import (M2)
```
POST asset (rights, size) -> PUT chunks (Upload-Offset) -> upload.part complete
  -> job media.import: sha256 -> ffprobe + analyse() -> decode head/tail -> rename to `source`
       -> ONE ffmpeg pass: proxy.mp4 (540p, 1 s GOP) + thumbs.jpg (sprite) + audio.wav (16 kHz mono)
       -> waveform.json (20 peaks/s, block-wise, O(1) memory) -> asset READY
```
Storage per video: `storage/projects/{pid}/assets/{aid}/{source, derived/{proxy.mp4,thumbs.jpg,audio.wav,waveform.json}}`.
All FFmpeg calls on user files use `-protocol_whitelist file -format_whitelist mov,matroska,webm,avi,mpegts,flv`.
Input errors (`MediaError`) are never retried and free the uploaded bytes; tool/IO errors are retried.
If an import job dies without updating its video (worker lost on last attempt, cancel), the API reconciles the
video to `failed` on the next read. A housekeeping thread (startup + hourly) expires abandoned uploads.

## Not yet implemented
Per-install API token (planned with the web UI in M4), AI pipeline (M3),
editor/UI (M4), rendering (M5), multi-user auth and PostgreSQL support (v1.0).
