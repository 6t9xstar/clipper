# Architecture — as implemented (M5, M6 in progress)

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
  and both are checked against the same vectors in `testdata/framing_cases.json`. Since M5 the preview uses the
  bundled fonts and the renderer's line metrics (see below).
- **Concurrency:** clips and transcripts carry a `version`; writes use `If-Match` and an atomic compare-and-swap
  `UPDATE … WHERE version = ?`. The editor keeps unsaved changes and offers "load the latest version" on `412`.
- Tests: `backend/tests/test_edit.py`, `frontend/src/lib/lib.test.ts` (Vitest), `frontend/e2e/` (Playwright
  against a real server with a generated video).

## Render and export (M5)
```
POST /clips/{id}/exports -> settings snapshot (+ sha256) -> Export row + job "render.export"
render.export: ASS script + font copied to <export>/work
            -> loudnorm measure -> AudioPlan (linear gain | gain + capped limiter, refined with ebur128)
            -> ffmpeg: -ss/-t before -i, crop|fit -> scale(bt709) -> fps -> ass(fontsdir) ; audio plan
               libx264 crf + maxrate, AAC 48 kHz, -progress pipe:1 -> job progress
            -> verify (ffprobe + ebur128) -> clip.mp4 / clip.srt / clip.vtt / clip.json -> done
```
- Code: `render/graph.py` (filter graphs, qualities, audio policy, parsers), `render/ass.py` (ASS script),
  `render/subs.py` (SRT/VTT), `render/fontinfo.py` (reads TrueType tables: metrics, cmap, advance widths),
  `render/export.py` (the job), `api/exports.py`.
- **Fonts:** Montserrat (Black/ExtraBold/Bold) and Anton, SIL OFL, in `render/fonts/`; served to the UI at `/fonts`
  as `@font-face` families so the preview and the video use the same files.
- **Layout parity:** libass sizes a font so winAscent+winDescent = the ASS size, while CSS uses the em square.
  Presets are defined as CSS px on the 1080×1920 frame; the ASS size is `size / css_scale` and every line is its own
  event placed with `n5\pos` at the same centre the preview uses, with a baseline correction for the two
  metric sets. Lines that would be wider than the frame minus margins are scaled down (`fit`, from the font's
  advance widths) identically in both. A test renders each preset with FFmpeg and compares the glyph rows with the
  maths the browser uses (≤ 3 px).
- **Paths in the filter graph:** FFmpeg runs with `cwd` = the work folder and refers to `captions.ass` and `fonts`
  by relative name, so no user path is ever escaped into a filter string.
- **Storage:** `projects/{p}/assets/{a}/exports/{e}/`; the temporary `work/` folder is removed after the job.
  A crashed render is retried by the queue (lease expiry); a cancelled one kills FFmpeg's process tree.
- Tests: `backend/tests/test_render.py` (unit, golden end-to-end renders on generated media, API rules),
  `frontend/e2e/editor.spec.ts` (export + download), `frontend/e2e/speech.spec.ts` (full flow with real speech
  recognition on OS text-to-speech audio).

## Text overlays (M6)
- `edit/overlays.py`: three styles (title, callout, label) built on the caption `Preset` type and fonts, and
  `layout()`: balanced wrapping (user line breaks kept), shrink-to-fit per line, clamping the block inside the
  frame margins, and cutting times to the clip. The layout is computed only on the server — the editor previews
  unsaved overlays through `POST /overlays/layout` — so preview and export agree.
- The export snapshot stores the laid-out overlays; `render/ass.py` writes them into the same ASS script as the
  captions, on layers 2–3 above them, with their own styles (`Ov<style>`, `Ov<style>Box`). A clip with overlays
  but captions off still runs the `ass` filter. The manifest lists the overlays.
- Tests: `tests/test_overlays.py` (layout, validation, rendered glyph centre and baseline vs preview maths within
  3 px, export showing the overlay only in its time window), Playwright (add, drag, persist, export).

## Voiceover and ducking (M6)
- Takes: `api/voice.py` streams the upload to `projects/{p}/assets/{a}/voice/{id}/`, `media/voice.py` converts
  it to FLAC and measures loudness (ebur128), speech segments (20 ms RMS frames within 28 dB of the take's loud
  parts, short gaps bridged) and peaks. Table `voice_tracks` (migration 0006); deleting the clip removes them.
- Ducking maths lives in `edit/voice.py` (placement → merged clip-time segments, `duck_gain`, and the same
  function as an FFmpeg `volume` expression with a balanced `max()` tree). `frontend/src/lib/ducking.ts` ports it
  for the preview; both are checked against `testdata/ducking_cases.json`.
- Render: the audio is now a `filter_complex` mix — `[0:a]` (levelled, user gain, ducked) + each take
  (`adelay`) → `amix` → trimmed/padded to the clip → the loudness plan; the two-pass measurement runs on the mix.
  Take files are FFmpeg inputs (`-i`, FLAC-only whitelist), never paths inside the filter string.
- Preview: hidden `<audio>` elements follow the playhead; the video's volume follows `duck_gain`. Browser volume
  cannot exceed 1, so boosts above 0 dB are only heard in the export.
- Recording: `MediaRecorder` (Opus in WebM/Ogg, or MP4) while the clip plays from the playhead, muted by default
  to avoid echo; it stops at the clip end. Tests: `tests/test_voice.py` (speech detection, vectors, API, an export
  whose 440 Hz original measures ~12 dB lower under a 1 kHz voice, muted original), Playwright with Chromium's
  fake microphone.

## Not yet implemented
Face-following / animated framing (crop is static per clip), hardware encoders (NVENC/QSV/VideoToolbox),
per-platform metadata, batch export, multi-user auth and PostgreSQL support (v1.0).
