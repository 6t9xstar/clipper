# Architecture — as implemented (M4)

Target architecture: [PHASE1_PLAN.md §6](PHASE1_PLAN.md). This page describes only what exists in code today.

```
clipper serve ──► FastAPI app (backend/clipper/api)          clipper worker ──► Worker loop(s)
                    │  LocalGuardMiddleware (Host/Origin/token)                  │
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
- Bind to 127.0.0.1 by default; `Host` allow-list (DNS rebinding), `Origin` check on writes (CSRF) and a
  per-install access token on `/api/*` (other local programs and web pages cannot call the API).
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

## AI analysis (M3)
```
POST analyze -> job ai.analyze (backend/clipper/ai):
  transcript  asr.py      faster-whisper, word timestamps, VAD; GPU (large-v3-turbo) else CPU (small);
                          GPU load failure falls back to CPU and is noted   -> derived/transcript.json + transcripts row
  shot cuts   scenes.py   FFmpeg scdet on the proxy (10 fps, 160 px)        -> derived/scenes.json
  scoring     highlights.py  pure functions: sentences -> candidate runs -> features -> non-overlapping top-K
  clips       replace origin=ai/status=suggested rows; keep approved/rejected/manual
```
Each step is cached, so a retry or re-analysis does not transcribe again. Model files are cached in
`data/models` (Hugging Face layout). Known limits: scdet misses gradual transitions and cuts between shots of
equal brightness; keyword features are English-only.

## Web UI and editor (M4)
```
frontend/ (React 19 + TypeScript, Vite, TanStack Query, Tailwind)  --npm run build-->  backend/clipper/web/dist
clipper serve: /api/*  -> API (token required)
               /static -> built JS/CSS          (Vite assetsDir; "/assets/<id>" is an app route)
               anything else -> index.html + token cookie + strict CSP (script/style 'self' only)
```
- **Token:** `data/api_token` (random, created once, kept across restarts). The middleware accepts it as the
  `X-Clipper-Token` header or the `clipper_token` cookie (`HttpOnly`, `SameSite=Strict`), compared in constant time.
- **Preview = export:** caption grouping and line-breaking run only on the server (`edit/captions.py`); the UI
  draws the groups it receives. The 9:16 crop maths exists twice (`edit/framing.py`, `frontend/src/lib/framing.ts`)
  and both are checked against the same vectors in `testdata/framing_cases.json`. Font metrics in the preview
  are approximate until the renderer (M5) calibrates them.
- **Concurrency:** clips and transcripts carry a `version`; writes use `If-Match` and an atomic compare-and-swap
  `UPDATE … WHERE version = ?`. The editor keeps unsaved changes and offers "load the latest version" on `412`.
- Tests: `backend/tests/test_edit.py`, `frontend/src/lib/lib.test.ts` (Vitest), `frontend/e2e/` (Playwright
  against a real server with a generated video).

## Not yet implemented
Rendering/export (M5), face-following framing, multi-user auth and PostgreSQL support (v1.0).
