# Phase 1 — Technical Implementation Plan
**Project codename:** Clipper (working name; final name TBD — check trademark conflicts before release)
**Plan date:** 2026-11-15 · **Status:** Awaiting approval · **No application code has been written.**

Legend used throughout: **[V]** verified against a dated source in this research pass · **[A]** engineering assumption · **[U]** unverified / needs prototype or check · **[R]** future risk.

---

## 1. Product Specification

### 1.1 Users
| Persona | Need | Skill |
|---|---|---|
| **Solo creator** (podcaster, streamer, educator) | Turn own 30–180 min recordings into 5–20 Shorts/week | Beginner; wants "one button" |
| **Pro editor / small agency** | Batch clips for clients they have contracts with; brand templates | Advanced; wants timeline control, presets, batch export |
| **Gaming creator** | Highlights + commentary from own gameplay recordings | Intermediate; wants game templates, facecam layouts |
| **Self-hoster / org** (school, media team) | Private, on-prem processing, multiple users | Technical admin |

### 1.2 Problem
Manually scrubbing long videos, cutting, reframing to 9:16 and captioning takes 30–90 min per short. Existing SaaS tools (Opus Clip, Vizard, etc.) are paid, upload your media to third parties, and are opaque about why clips were chosen.

### 1.3 Core workflow (MVP)
```
Install → Create project → Import local file (+ rights attestation)
 → Analyze (transcribe, scenes, faces, silence, scoring)
 → Review suggested clips (reason, score, transcript)
 → Edit (trim, caption text/style, framing, aspect)
 → Preview → Export MP4 (+SRT/VTT) → Open folder / publish checklist
```

### 1.4 Product boundaries (explicit non-goals)
- **No** downloading from YouTube/TikTok/etc. in MVP or v1. URL import is limited (v1.x) to direct-file URLs and sources with an explicit permissive license (e.g. user's own cloud storage, Internet Archive items marked CC/PD). No yt-dlp integration shipped by default. [Decision — reduces legal risk and ToS conflict]
- **No** DRM, geo-block, login, or Content ID circumvention; no "anti-detection" features (mirroring, pitch-shifting to evade, etc.).
- **No** generative content that fabricates dialogue or events. LLMs may *rank/describe* existing transcript segments only; titles/descriptions are suggestions clearly marked as AI-generated and editable.
- **No** earnings/views promises anywhere in UI or docs.
- Not a full NLE (Premiere replacement). Single-track clip editing with overlays.

---

## 2. Feature Prioritization

### MVP (milestone M1–M5) — "one creator, one machine, works end to end"
- Local file import (MP4/MOV/MKV/WebM), ffprobe validation, size/duration limits, rights attestation checkbox stored per asset
- Proxy generation (low-res H.264 for UI preview) + waveform + thumbnails
- Transcription with word timestamps (faster-whisper, CPU int8 default, GPU optional)
- Scene/shot detection (PySceneDetect)
- Silence detection (ffmpeg `silencedetect` + VAD from Whisper pipeline)
- Highlight scoring — **heuristic engine, no LLM required** (see §4.6)
- Optional LLM re-ranking via local Ollama **or** user-supplied OpenAI-compatible endpoint (off by default, disclosed)
- Sentence-boundary-aware clip snapping
- Clip gallery with score + human-readable reason
- Editor: trim handles, transcript-based trimming, caption text editing, 4 caption presets, font/color/size/position
- Face detection + smoothed crop path for 9:16 (MediaPipe / YuNet) with fallback to center crop + manual keyframe override
- Aspect presets 9:16, 1:1, 16:9; blurred-background "fit" layout
- Export: MP4 H.264/AAC, platform presets (Shorts/TikTok/Reels), loudness normalization (EBU R128, −14 LUFS), burned-in ASS captions, SRT/VTT sidecar
- Job queue with progress, cancel, retry, crash recovery
- Job history, clear error messages, settings (models, paths, limits, retention)
- Delete project + all derived files; retention policy
- Single-user local mode, no auth (bound to 127.0.0.1)

### First production release (v1.0)
- Batch export, render queue concurrency settings
- Speaker diarization (pyannote, opt-in; requires accepting HF model terms) [U licensing per model]
- Active-speaker framing (diarization + face tracks) for 2-person podcasts; split-screen layout (facecam over gameplay)
- Keyword highlighting, animated word-by-word captions, emoji-free by default
- Brand kit: logo watermark, intro/outro clips, progress bar, custom fonts (user-supplied, license reminder)
- Templates incl. gaming templates (§5)
- Commentary/voiceover recording track (browser MediaRecorder) + audio ducking — the main "transformation" tool
- Text/annotation overlays (callouts, chapter titles)
- Monetization-readiness checklist per clip (§3)
- Multi-user self-hosted mode: local accounts, argon2 passwords, sessions, per-user storage quotas
- Docker Compose deployment with Postgres + Redis option
- Hardware auto-detect (CUDA/NVENC/QSV/VideoToolbox) with tested fallback
- Translations of UI (i18n scaffold), caption translation is **future**

### Future releases
- YouTube Data API upload (OAuth, user's own channel; requires Google app verification) — **publishing is manual (export + checklist) until then**
- TikTok Content Posting API / Instagram Graph API (both require app review) [U]
- Analytics import (YouTube Analytics API) for "performance tracking"
- Desktop installer (Tauri shell around the same web app)
- B-roll from user's own library, licensed music library integration (user-supplied or CC0 sources)
- Caption translation, multilingual dubbing (only user-voice, consent-gated)
- Plugin API for custom scorers/templates
- Kubernetes/Helm chart; S3 storage backend at scale

---

## 3. Copyright, Permissions & Monetization Readiness

### 3.1 Permission model (implemented, not just text)
- Every imported asset requires a **source declaration**: `own_content | licensed | permission_from_owner | public_domain_or_cc | other` + optional note/licence URL. Stored in DB, shown on the asset, included in an export manifest (`clip.json`).
- First-run disclosure screen + `docs/LEGAL_USE.md`: what the tool does/doesn't do, fair use is jurisdiction-dependent and not legal advice.
- Third-party music: only user-supplied files; UI requires licence attestation; bundled audio = none, or CC0 only with provenance in `NOTICE`.

### 3.2 Monetization-readiness checklist (per clip, advisory)
Automatic signals + manual checkboxes, producing "Ready / Needs attention", never a prediction of earnings:
1. Source rights declared and not `other` (auto)
2. Original voice commentary present / % of clip with user voiceover (auto, if commentary track used)
3. Added context: annotations/titles present (auto)
4. Not a near-duplicate of another exported clip from same source (auto: perceptual hash + transcript overlap)
5. Music licensed (manual)
6. Game publisher policy reviewed (manual, gaming templates)
7. Title/description written or reviewed by user (manual)
8. Disclosure of synthetic/altered content where required by platform (manual) [V: YouTube requires altered/synthetic disclosure — re-check current wording at build]

**Verified context [V 2026-11-15, third-party summaries; official page must be re-read before release]:** YouTube's reused-content rule disallows monetizing content repurposed "without adding significant original commentary or substantive changes," evaluated channel-wide, and applies even with the owner's permission. Monetization policies were restructured in July 2026 (categories now include "Generic or Repetitive Content"). Sources: [cliptude](https://cliptude.com/learn/youtube-reused-content-policy/), [keepmonetized](https://keepmonetized.com/guides/reused-vs-inauthentic-content/), [lilachbullock](https://www.lilachbullock.com/youtube-channel-monetization-policies/). **Implication:** permission alone ≠ monetizable; the product's value must be helping *transformation* (commentary, context), which is why voiceover and annotations are v1.0 requirements, not extras.

---

## 4. Technical Research & Stack Decisions (researched 2026-11-15)

### 4.0 Architectural shape decision
**Selected:** Modular monolith — one Python API process + one or more worker processes sharing a codebase, a SPA frontend served by the API. Local mode = single command, SQLite, filesystem. Self-hosted mode = same code, Postgres, Redis-optional, Docker Compose.
**Rejected:** microservices (unjustified ops cost), Electron-first desktop (heavy, but Tauri wrapper planned later), pure-Node backend (AI/video ecosystem is Python-native).

### 4.1 Backend: **Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic**
- Why: the whole AI stack (faster-whisper, PySceneDetect, MediaPipe, pyannote) is Python; FastAPI gives OpenAPI docs for free; async for SSE progress.
- Alternatives: Django (heavier, ORM fine, less natural for typed APIs), Litestar (good but smaller community), Node/NestJS (would need Python sidecar anyway).
- Perf: API is not the bottleneck; workers are.
- License: FastAPI MIT, SQLAlchemy MIT, Alembic MIT, Pydantic MIT.
- Python 3.12 chosen over 3.13 because WhisperX 3.8.6 supports 3.10–3.13 [V] but some CV wheels lag on newest versions [A]; revisit at M1.
- Packaging: **uv** (lockfile `uv.lock`, fast reproducible installs). Alternative Poetry.
- Migration strategy: pin minor versions, Renovate PRs, CI matrix.

### 4.2 Frontend: **React 19 + TypeScript + Vite + TanStack Query + Tailwind + shadcn/ui (Radix)**
- Why: largest ecosystem, accessible primitives (Radix), simple static build served by FastAPI (no Node at runtime).
- Alternatives: Next.js (SSR unnecessary, adds Node runtime), SvelteKit (smaller hiring pool for contributors), Vue (fine; React picked for component ecosystem — video players, timeline libs).
- Video preview: native `<video>` on proxy files + custom canvas/DOM caption overlay that mirrors the ASS render (preview ≈ export; the *export* is authoritative). Timeline: custom component (existing OSS timeline libs evaluated at M4 — [U]).
- License: all MIT.

### 4.3 Video engine: **FFmpeg 7.x (CLI, invoked via subprocess with argument lists)**
- Why: industry standard; filters for crop, scale, pad, boxblur, `ass` subtitles (libass), `loudnorm`, `silencedetect`; HW encoders (NVENC, QSV, VideoToolbox, VAAPI).
- Alternatives: MoviePy (slow, frame-by-frame Python), GStreamer (powerful, harder), PyAV (used selectively for frame reading in face tracking).
- **Licensing [important]:** FFmpeg is LGPL by default; builds with libx264 are **GPL**. We will **not bundle** FFmpeg binaries in the repo; Docker image installs distro FFmpeg (GPL build) — that's acceptable because the project license (below) is compatible, and we document it in `NOTICE`. Windows users get documented install via winget (`Gyan.FFmpeg`) [U verify package id at M1].
- Dynamic per-clip crop path implemented via `sendcmd` / expression-based crop, or by piping frames via PyAV for complex paths [U — prototype P2].

### 4.4 Speech recognition: **faster-whisper 1.2.1 (CTranslate2)** default; WhisperX alignment optional
- [V] faster-whisper latest stable v1.2.1 (2025-10-31), no 2026 release as of Oct 2026 — stable, not abandoned. [GitHub releases](https://github.com/SYSTRAN/faster-whisper/releases), [SmartScope 2026](https://smartscope.blog/en/generative-ai/foundations/whisper-local-cpu-implementation/)
- [V] WhisperX 3.8.6 (2026-05-25): Python 3.10–3.13, PyTorch 2.8, faster-whisper ≥1.2, pyannote.audio 4; 3.8.2/3.8.3 yanked for timestamp regressions. [PyPI](https://pypi.org/project/whisperx/) → shows churn; **we pin and keep WhisperX optional**.
- Why faster-whisper: MIT; native word timestamps; built-in Silero VAD; int8 CPU works on ordinary PCs; ~4× faster than openai-whisper [A from upstream benchmarks].
- Models: default `small` (CPU) / `large-v3-turbo` (GPU). Whisper weights are MIT [V general knowledge; re-verify model card].
- Alternatives: whisper.cpp (great CPU perf, C++ binding complexity — keep as possible future backend), NVIDIA Parakeet/Canary (strong English accuracy, CC-BY license, NeMo dependency heavy), cloud APIs (optional plugin only).
- Limitations: hallucinations on silence/music (mitigated by VAD, `condition_on_previous_text=False`), word timestamps imperfect on fast speech; gaming audio with music/SFX degrades accuracy [R].

### 4.5 Scene detection: **PySceneDetect** (BSD-3) `ContentDetector` + `AdaptiveDetector`
- Alternatives: TransNetV2 (more accurate, needs TF/torch model; future option), ffmpeg `select=gt(scene,…)` (cruder).
- Run on downscaled proxy for speed.

### 4.6 Highlight scoring (core IP of the project)
**Two-stage, explainable:**
1. **Candidate generation:** sliding windows over transcript sentences sized to target duration (e.g. 20–60 s), snapped to sentence boundaries and scene cuts, trimmed of leading/trailing silence.
2. **Heuristic scoring (always available, CPU, deterministic):** features with weights stored in the template:
   - speech density & energy (RMS loudness peaks, laughter/excitement proxy via loudness variance)
   - hook quality: first 3 s contains a question/strong statement (rule-based patterns + sentence position)
   - self-containedness: penalize starting with pronouns/conjunctions ("and", "so", "he") without antecedent
   - keyword/topic salience (KeyBERT-lite: TF-IDF / YAKE (Apache-2.0? [U]) over full transcript)
   - visual activity (scene-change rate, motion magnitude)
   - for gaming: audio spikes, killfeed/game SFX loudness (no game-specific vision models in v1)
   - Reasons are generated from the top-contributing features ("Strong opening question; high vocal energy at 0:12; self-contained topic: 'pricing strategy'").
3. **Optional LLM re-rank:** send *transcript text only* (never video) of top-N candidates to local Ollama (default model e.g. Llama/Qwen small instruct, licence shown in UI) or a user-configured OpenAI-compatible endpoint. Prompt constrains output to JSON: `{id, score, reason, suggested_title}`; reasons must quote transcript; titles flagged as AI suggestions. Validation rejects outputs referencing timestamps/content not in input.
- Honest limitation: "virality" can't be predicted reliably; UI language is "suggested", "potentially engaging". [A]

### 4.7 Face detection & framing: **MediaPipe Face Detection / OpenCV YuNet**
- MediaPipe (Apache-2.0) BlazeFace; OpenCV YuNet (Apache-2.0/MIT model). Run at 5 fps on proxy → tracks via IoU + Kalman/One-Euro smoothing → crop path with max pan speed & dead-zone (avoid jitter).
- Active speaker (v1.0): match diarization speaker turns to face tracks via mouth-movement heuristic / audio-visual sync [U — prototype P3; fallback: user picks speaker→face mapping].
- Alternatives: InsightFace (model licence non-commercial — **rejected**), YOLO-face via Ultralytics (**AGPL — rejected** for license reasons).
- Gaming: no face → template uses either center/HUD-safe crop or "facecam region + gameplay region" split layout defined by user drawing 2 rectangles once.

### 4.8 Speaker diarization (v1.0, opt-in): **pyannote.audio 4 + speaker-diarization-community-1**
- [V] used by WhisperX 3.8.6. Requires Hugging Face token and accepting model conditions [U exact licence of community-1 — must verify before shipping; if non-permissive, ship as user-installed optional].

### 4.9 Captions: **ASS (Advanced SubStation Alpha) generated by us → burned via libass in FFmpeg**
- Supports per-word karaoke highlighting (`\k`, colour overrides), fonts, outlines, positions. SRT/VTT export from same word-level model.
- Fonts: bundle only OFL-licensed fonts (e.g. Inter, Montserrat, Bebas Neue — verify each OFL) with licence files.

### 4.10 Database: **SQLite (WAL) local / PostgreSQL 16+ self-hosted**, via SQLAlchemy + Alembic
- Same migrations for both; CI runs tests on both. Avoid Postgres-only features in MVP.

### 4.11 Job queue: **Custom DB-backed queue (table `jobs`) with worker processes** — not Celery
- Why: local mode must have zero extra services (no Redis install on Windows). DB queue with `SELECT … FOR UPDATE SKIP LOCKED` (Postgres) / single-writer lock (SQLite), heartbeats, leases, retries with backoff, cancellation flag, idempotent steps.
- Alternatives: Celery (needs broker, heavy), RQ/Dramatiq/Arq (need Redis), Huey (SQLite support, nice — **strong alternative, evaluate in P1**), Temporal (overkill).
- Each pipeline = DAG of steps (`probe → proxy → transcribe → scenes → faces → score`); step outputs are content-addressed artifacts so retries resume.
- Cancellation: worker checks flag between chunks; FFmpeg subprocess killed with process-group termination (Windows: `CREATE_NEW_PROCESS_GROUP` + taskkill tree).
- Progress: workers write progress to DB; API streams via SSE.

### 4.12 Storage: **`StorageBackend` interface** — `LocalFS` (MVP), `S3Compatible` (v1.0, MinIO/AWS/R2)
- Layout: `data/projects/{project_id}/{source,proxy,artifacts,exports}/`, IDs are UUIDv7; user-provided filenames never used as paths.

### 4.13 Auth: none in local mode (bind 127.0.0.1 + CSRF-safe same-origin + random per-install API token); v1.0 multi-user: local accounts (argon2id), httpOnly session cookies; OIDC future.

### 4.14 Observability: structlog JSON logs (no transcript/file content), `/healthz` & `/readyz`, Prometheus `/metrics` (optional), Sentry-compatible error reporting **off by default** (opt-in, self-host GlitchTip possible).

### 4.15 Testing/tooling: pytest, pytest-asyncio, hypothesis (time math), Playwright (E2E), Vitest + Testing Library; ruff (lint+format), mypy --strict (backend), tsc --noEmit, ESLint, Prettier; pre-commit.

### 4.16 CI/CD: GitHub Actions — lint/type/test matrix (Ubuntu/Windows/macOS, Py 3.12), E2E on Ubuntu with real FFmpeg and tiny model (`tiny.en`), CodeQL, `pip-audit` + `npm audit`/osv-scanner, Trivy for images, Syft SBOM (CycloneDX), Release Please for changelog + tags, multi-arch image to GHCR with cosign signing.

### 4.17 Project license: **AGPL-3.0 vs GPL-3.0 vs Apache-2.0 decision needed from you**
- Recommendation: **Apache-2.0** for code (max adoption), while documenting that the Docker image bundles GPL FFmpeg (aggregation, allowed). If you want to prevent closed SaaS forks, choose **AGPL-3.0**. Note: Ultralytics-style AGPL deps are excluded either way.

---

## 5. GTA VI & Gaming Strategy

### Verified facts [V 2026-11-15]
- GTA VI launches **Thursday, 19 Nov 2026** on PS5 and Xbox Series X|S; pre-load open; **no PC date announced**. Prior delays: 2025 → May 26 2026 → Nov 19 2026. Take-Two's 9 Nov 2026 earnings still list the date. Sources: [Rockstar Newswire](https://www.rockstargames.com/newswire/article/ak3ak31a49a221/grand-theft-auto-vi-is-now-set-to-launch-november-19-2026), [TechRadar](https://www.techradar.com/gaming/grand-theft-auto-6-delayed-again-but-itll-still-ship-in-2026), [tracker.gg](https://tracker.gg/gta6/release-date).

### Unverified — must check before shipping gaming templates [U]
- Rockstar's current policy on posting copyrighted material (historically allows non-commercial-style gameplay videos with platform monetization, prohibits leaks/pre-release content, some restrictions on mods/online). Fetch of the official Rockstar support article timed out during this research; **must be read and quoted in docs at M6**.
- Console capture restrictions (PS5/Xbox may block capture of certain story scenes) [U].
- Search demand/saturation: launch-window demand will be extreme and saturation also extreme; reaction/highlight clips of the same missions will be heavily duplicated → reused/generic-content risk is *high* for raw gameplay clips. [A]

### Strategy
- Gaming = templates on a niche-agnostic engine; GTA is never hard-coded.
- Templates: Gameplay highlight (facecam split), Funny moments (audio-spike scoring), Tutorial/guide (step captions + chapter overlays), Mission explainer (annotation callouts + voiceover required), Character analysis (voiceover + still frames), News/discussion (talking-head + B-roll of own footage).
- Every gaming template enables the checklist item "Publisher video policy reviewed" and nudges toward commentary.
- Other niches at v1.0: Podcast/interview, Education/lecture, Talking-head vlog, Webinar/product demo.

---

## 6. Architecture Design

### 6.1 System diagram
```
                ┌──────────────────────────── Browser (React SPA) ───────────────────────────┐
                │ Dashboard · Import · Analysis · Clip gallery · Editor/Preview · Export · Jobs│
                └───────────────┬──────────────────────────────────────▲─────────────────────┘
                     REST /api/v1 (JSON)  + tus/chunked upload        SSE /api/v1/events
                                │                                      │
┌───────────────────────────────▼──────────────────────────────────────┴─────────────┐
│ API process (FastAPI/Uvicorn)                                                       │
│  routers · validation · auth(opt) · upload handler · job enqueue · static SPA       │
└──────┬───────────────────────────┬────────────────────────────┬────────────────────┘
       │ SQLAlchemy                 │ StorageBackend             │ enqueue/poll
┌──────▼──────┐            ┌───────▼────────┐          ┌────────▼──────────────────────┐
│ SQLite /    │◄──────────►│ LocalFS / S3   │◄────────►│ Worker process(es) ×N          │
│ Postgres    │  jobs,     │ source, proxy, │          │  pipeline steps:               │
│ (jobs table │  projects  │ artifacts,     │          │  probe→proxy→ASR→scenes→faces  │
│  = queue)   │            │ exports        │          │  →silence→score→(LLM)→render   │
└─────────────┘            └────────────────┘          │  ffmpeg / faster-whisper /     │
                                                       │  PySceneDetect / MediaPipe     │
                                                       └───────┬────────────────────────┘
                                                               │ optional, opt-in
                                                     ┌─────────▼──────────┐
                                                     │ Ollama (local) or  │
                                                     │ OpenAI-compatible  │
                                                     │ endpoint (text only)│
                                                     └────────────────────┘
```

### 6.2 Components
| Component | Responsibility |
|---|---|
| `api` | HTTP, validation, authz, upload, enqueue, SSE, serves SPA |
| `core/domain` | Entities, time-range math, clip rules (pure, heavily unit-tested) |
| `pipeline` | Step definitions, DAG, artifact caching |
| `media` | FFmpeg command builders (pure functions → argv lists), ffprobe parsing |
| `ai` | ASR adapter, scene, face, diarization, scoring, LLM adapter — each behind an interface |
| `render` | Edit decision list (EDL) → ffmpeg filter graph; ASS caption generator |
| `jobs` | Queue, leases, retry, cancel, recovery |
| `storage` | Backends, path safety, retention sweeper |
| `web` | SPA |

### 6.3 Data model (core tables)
`projects(id, name, created_at, settings_json)` · `assets(id, project_id, kind, storage_key, sha256, duration_ms, width, height, fps, codecs, rights_type, rights_note, status)` · `transcripts(asset_id, language, model, words_jsonb)` · `scenes(asset_id, start_ms, end_ms)` · `face_tracks(asset_id, track_id, samples_blob)` · `clips(id, project_id, asset_id, start_ms, end_ms, score, reasons_json, status[suggested|approved|rejected], edit_json, version)` · `exports(id, clip_id, preset, settings_hash, storage_key, status)` · `jobs(id, type, payload_json, status, attempts, lease_until, progress, error_code, error_detail, cancel_requested, parent_id)` · `templates(id, kind, json)` · `users` (v1.0) · `audit_events` (v1.0).
All times are integer **milliseconds**; frame-accurate snapping at render using source fps.

### 6.4 Video-processing workflow
1. Upload → temp file in `data/tmp/{uuid}` (streamed, size-limited) → sha256 → ffprobe (timeout 30 s) → validate container/codec whitelist, duration/resolution caps → move to `source/`.
2. Proxy: 540p H.264 CRF 28, keyframe every 1 s (scrubbing) + 16 kHz mono WAV for ASR + waveform peaks JSON + thumbnail sprite.
3. Render: clip EDL (source range, crop path, layout, captions, overlays, audio tracks) → deterministic filter graph → encode → verify output via ffprobe (duration ±1 frame, streams present) → mark done. Settings hash stored → reproducible.

### 6.5 AI-processing workflow
```
audio.wav ─► VAD ─► faster-whisper (word ts) ─► sentence segmentation ─┐
proxy.mp4 ─► PySceneDetect ─────────────────────────────────────────────┤
proxy.mp4 ─► face detect @5fps ─► tracks ─► smoothed crop paths ────────┤
audio.wav ─► loudness/energy curve, silencedetect ──────────────────────┤
                                                                         ▼
                                  candidate windows ─► heuristic score ─► top-N
                                                                         ▼
                                           (optional) LLM rerank on transcript text
                                                                         ▼
                                           clips(status=suggested, reasons)
```
Long videos processed in chunks (ASR naturally streams; faces on proxy) → memory bounded.

### 6.6 API design (v1, REST, OpenAPI auto-generated)
```
POST   /api/v1/projects                     GET /api/v1/projects[/{id}]   DELETE …
POST   /api/v1/projects/{id}/assets         (chunked/tus upload, rights fields)
GET    /api/v1/assets/{id}                  GET /api/v1/assets/{id}/proxy (Range)
POST   /api/v1/assets/{id}/analyze          {settings} → job
GET    /api/v1/assets/{id}/transcript       PATCH (word edits)
GET    /api/v1/projects/{id}/clips          POST (manual clip)  PATCH /clips/{id} (edit_json, If-Match version)
POST   /api/v1/clips/{id}/exports           {preset} → job      POST /api/v1/exports/batch
GET    /api/v1/exports/{id}/download        GET /api/v1/clips/{id}/captions.{srt|vtt}
GET    /api/v1/jobs?status=                 POST /api/v1/jobs/{id}/cancel  POST /jobs/{id}/retry
GET    /api/v1/events                       (SSE: job progress, status changes)
GET/PUT /api/v1/settings                    GET /api/v1/system/capabilities (GPU, encoders, models)
GET    /healthz  /readyz  /metrics
```
Errors: RFC 9457 problem+json with stable `code` (e.g. `MEDIA_UNSUPPORTED_CODEC`) and user-facing `hint`.

### 6.7 Background-job design
- States: `queued → running → (succeeded | failed | cancelled)`; `failed_retryable` → backoff (30 s, 2 m, 10 m) max 3.
- Leases 60 s renewed by heartbeat; on worker crash, lease expires → job re-queued; steps idempotent via artifact existence + checksum.
- Resource classes: `cpu_heavy`, `gpu`, `io`; per-class concurrency limits (default: 1 GPU job, cores/2 CPU threads for ASR, 1 render on laptops).
- Startup recovery: running jobs with expired leases → requeued; orphan temp files swept.

### 6.8 Deployment architecture
- **Local (default):** `uv run clipper` (or `clipper` from pipx) starts API + 1 worker in one supervisor; opens browser. Docker alternative: `docker compose up`.
- **Self-hosted:** Compose: `api`, `worker` (×N; GPU worker with NVIDIA Container Toolkit), `postgres`, optional `minio`, reverse proxy (Caddy) with TLS. Documented path → Kubernetes (future).
- Models downloaded on first use to `data/models` with checksum verification; offline install docs.

---

## 7. Repository Structure
```
clipper/
├─ README.md  LICENSE  NOTICE  CHANGELOG.md  CONTRIBUTING.md  CODE_OF_CONDUCT.md  SECURITY.md
├─ .env.example  .gitignore  .editorconfig  .pre-commit-config.yaml  renovate.json
├─ pyproject.toml  uv.lock                      # backend deps & tool config (ruff, mypy, pytest)
├─ backend/clipper/
│  ├─ api/            # FastAPI app, routers/, schemas/, deps, errors (problem+json)
│  ├─ core/           # config (pydantic-settings), domain models, timecode math, ids
│  ├─ db/             # SQLAlchemy models, session, repositories
│  ├─ migrations/     # Alembic env + versions/
│  ├─ jobs/           # queue, worker loop, leases, cancellation, recovery
│  ├─ pipeline/       # step DAG: probe, proxy, transcribe, scenes, faces, silence, score, render
│  ├─ media/          # ffprobe parsing, ffmpeg argv builders, hw-accel detection
│  ├─ ai/             # asr/, scenes/, vision/, diarization/, scoring/, llm/ (interfaces + impls)
│  ├─ render/         # EDL, filter-graph compiler, ASS generator, layouts, presets
│  ├─ storage/        # backends, safe paths, retention sweeper
│  ├─ security/       # upload validation, rate limits, auth (v1.0)
│  └─ cli.py          # `clipper serve|worker|migrate|doctor|purge`
├─ backend/tests/     # unit/, integration/, media/ (golden-render tests), fixtures/
├─ frontend/          # Vite React TS app: src/{pages,components,features,api,lib}, tests/, package.json, pnpm-lock.yaml
├─ e2e/               # Playwright tests
├─ templates/         # JSON caption styles, layouts, niche templates (podcast, gaming…)
├─ assets/fonts/      # OFL fonts + licence files only
├─ samples/           # generated test media script (ffmpeg testsrc + TTS-free synthetic audio) + CC0 sample w/ provenance
├─ deploy/            # docker/ (Dockerfile.cpu, Dockerfile.cuda), compose.yaml, compose.prod.yaml, Caddyfile
├─ scripts/           # bootstrap.{sh,ps1}, gen_sample_media, sbom, release helpers
├─ docs/              # architecture.md, adr/, api.md, configuration.md, install/{windows,macos,linux}.md,
│                     # deployment.md, troubleshooting.md, LEGAL_USE.md, models-and-licenses.md, threat-model.md
└─ .github/           # workflows/{ci,e2e,codeql,security,release}.yml, ISSUE_TEMPLATE, PR template, dependabot
```
Sample media: generated synthetically in CI (ffmpeg `testsrc2` + sine/speech-like audio); for ASR E2E, a short **CC0/public-domain** speech clip (e.g. LibriVox public-domain recording — LibriVox recordings are PD in the US [V general; verify per-file]) with provenance file.

---

## 8. Hardware & Infrastructure

| Tier | Spec | Expected (60-min 1080p source) [A — to be measured in P1] |
|---|---|---|
| **Minimum** | 4-core x86-64/ARM64 (AVX2), 8 GB RAM, 20 GB free SSD, no GPU | ASR `small` int8 ≈ 15–30 min; full analysis ≈ 25–45 min; render 45 s 1080×1920 clip ≈ 1–2 min (x264 `veryfast`) |
| **Recommended** | 8+ cores, 16 GB RAM, NVIDIA GPU 6–8 GB VRAM (RTX 3060+) or Apple Silicon M1+ (16 GB) | ASR `large-v3-turbo` on GPU ≈ 2–4 min; analysis ≈ 5–8 min; NVENC render ≈ 10–20 s/clip |
| **Server** | 16+ cores, 32–64 GB RAM, 1–2× 16–24 GB GPUs, NVMe | Several concurrent projects |

- Storage: source + proxy ≈ 1.3× source size; exports ~10–20 MB/min at 1080×1920 8 Mbps. Default retention: tmp 24 h, proxies kept until project deleted.
- Apple Silicon: faster-whisper has no Metal backend (CTranslate2 is CPU there) [A/U] → CPU int8 is decent; whisper.cpp Metal backend is a future option. VideoToolbox encoder for render.
- AMD GPU: render via VAAPI/AMF possible; ASR CPU-only [A].
- **Bottlenecks:** ASR (CPU), face detection on long videos (mitigated by 5 fps on proxy), x264 encoding, disk I/O for 4K sources.
- **Cloud cost estimates [A, verify at deploy time]:** CPU VM 8 vCPU/32 GB ≈ $150–250/mo; GPU (T4/L4 class) on-demand ≈ $0.35–1.00/h; spot cheaper. Storage S3-class ≈ $0.02/GB-mo + egress. Single-creator self-host on a home PC: $0.

---

## 9. Security, Privacy & Legal Review

### 9.1 Threat model (STRIDE summary)
| Threat | Vector | Mitigation |
|---|---|---|
| Malicious media → FFmpeg/libav CVE exploitation | crafted upload | ffprobe/ffmpeg run as unprivileged user, in container w/ no network for workers (self-host), resource limits (CPU time, memory via cgroups / job timeouts), codec/demuxer whitelist (`-f` explicit, `-protocol_whitelist file,pipe`), keep FFmpeg patched, Trivy scans |
| Path traversal | filename, storage keys, ASS font paths | server-generated UUID keys; `resolve()` + is-relative-to check; never use client filenames in paths |
| SSRF | future URL import, playlist/HLS refs inside files | `-protocol_whitelist file`; URL import (future) via allowlist + no private IP ranges |
| Command injection | ffmpeg args | argv lists only, no shell; ASS text escaped; filter-graph text escaped by builder with tests |
| Zip/decompression bombs, huge files | upload | stream with max bytes, max duration/resolution/fps, disk-space precheck |
| Local CSRF / DNS rebinding against localhost server | malicious website | bind 127.0.0.1, Host header check, Origin check, per-install token, SameSite cookies |
| Data leakage | logs, LLM endpoint | logs exclude transcripts/filenames (hashed IDs); external LLM disabled by default with explicit disclosure of what is sent; secrets only via env/secret files, redacted in logs |
| Account attacks (v1.0) | brute force | argon2id, rate limiting, lockout, secure cookies, CSP |
| Supply chain | deps, models | lockfiles w/ hashes, Renovate, pip-audit/osv, model SHA256 pinning, signed images, SBOM |
| XSS | transcript text, project names | React escaping, strict CSP, no `dangerouslySetInnerHTML` |

### 9.2 Privacy
Local-first; no telemetry by default; data deletion = hard delete of DB rows + files + derived artifacts (verified by test); configurable retention; export manifest contains no personal data beyond what user typed.

### 9.3 License review (to be finalized in `docs/models-and-licenses.md`)
| Component | License | Status |
|---|---|---|
| FastAPI, Pydantic, SQLAlchemy, Alembic, React, Vite, Tailwind, Radix | MIT/BSD | OK |
| faster-whisper / CTranslate2 | MIT | OK [V repo] |
| Whisper model weights | MIT | OK [verify model card] |
| PySceneDetect | BSD-3 | OK |
| MediaPipe | Apache-2.0 | OK |
| OpenCV / YuNet | Apache-2.0 | OK |
| pyannote.audio | MIT; **models gated, licence per model** | [U] |
| FFmpeg w/ libx264 | GPL-2.0+ | OK if not linked; documented in NOTICE |
| libass | ISC | OK |
| Ultralytics YOLO | AGPL-3.0 | **Excluded** |
| InsightFace models | non-commercial | **Excluded** |
| Ollama | MIT; LLM weights vary (Llama community licence, Qwen Apache/Qwen licence) | user-chosen, licence shown |
| Fonts | OFL-1.1 | OK, include licence |

---

## 10. Testing Strategy
| Level | What | Tooling | Gate |
|---|---|---|---|
| Unit | time math, sentence snapping, scoring features, ffmpeg argv/filter builders, ASS generator, path safety, job state machine | pytest, hypothesis | ≥85% backend core coverage |
| Integration | API + DB (SQLite & Postgres), queue leases/retry/cancel, real ffmpeg on synthetic media, faster-whisper `tiny` on PD speech clip | pytest, testcontainers | every PR |
| Media/golden | render outputs: dims, duration ±1 frame, fps, audio LUFS within ±1, caption presence (frame OCR-free check: pixel diff vs golden at known timestamps, SSIM ≥ threshold) | ffprobe, ffmpeg `ssim`, `ebur128` | every PR (small), nightly (full) |
| E2E | import → analyze → edit → export in browser | Playwright | every PR (tiny model) |
| Robustness | corrupted files (truncated MP4, wrong extension, zero-audio, VFR, rotated phone video, 10-bit HEVC, 4K60, 3 h file), unsupported inputs | curated generated corpus | nightly |
| Recovery | kill worker mid-job (SIGKILL), restart → resumes; cancel during ffmpeg; disk full simulation | integration | nightly |
| Concurrency | 3 projects analyzing + 10 exports queued | load script | release |
| Performance | benchmark matrix CPU/GPU on reference videos; regressions >20% flagged | pytest-benchmark + scripts | release |
| Security | CodeQL, pip-audit, osv-scanner, Trivy, ZAP baseline vs API, upload fuzzing (atheris on probe parsing) | CI | weekly + release |
| Clean install | fresh Windows/macOS/Ubuntu VMs follow README verbatim | GH Actions runners + manual checklist | release |
| Quality eval | highlight relevance: small human-labelled set (own/PD content), precision@5 vs baseline | scripts | per scoring change |

---

## 11. Risk Register
| # | Risk | L | I | Mitigation | Remaining uncertainty |
|---|---|---|---|---|---|
| R1 | Highlight suggestions feel poor → product not useful | M | H | Heuristics + optional LLM, labelled eval set, fast manual editing so bad suggestions are cheap | Quality on diverse content unknown until P4 |
| R2 | CPU-only processing too slow for beginners | M | H | `small`/`base` model choice, progress & ETA, proxy-based analysis, background queue | Measured numbers pending P1 |
| R3 | Dynamic crop jitter / wrong subject | M | M | smoothing, dead-zones, manual keyframe override, center-crop fallback | Multi-person scenes |
| R4 | Preview ≠ export (caption mismatch) | M | M | export is truth; "render preview" of 3 s segment; golden tests | Font metric differences browser vs libass |
| R5 | Windows installation pain (FFmpeg, CUDA, long paths) | H | H | bootstrap.ps1, `clipper doctor`, Docker option, tested on clean VM in CI | GPU driver variance |
| R6 | Dependency churn (WhisperX yanks, PyTorch/CUDA) | H | M | WhisperX optional; faster-whisper avoids PyTorch in core; lockfiles; Renovate | — |
| R7 | Users misuse tool for unauthorized content | H | H(legal/reputational) | no downloader, rights declaration, docs, no evasion features | Can't technically prevent local misuse |
| R8 | Platform policy changes (YouTube July 2026 restructure precedent) | H | M | checklist content in versioned JSON, docs dated, no promises | Ongoing |
| R9 | Model licence problems (pyannote gated models) | M | M | opt-in, user accepts terms, documented | Exact licence [U] |
| R10 | FFmpeg CVE via malicious upload | L–M | H | sandboxing, limits, patching | Zero-days |
| R11 | Scope creep (publishing, analytics, NLE features) | H | H | strict MVP gates, publishing APIs deferred | — |
| R12 | GTA VI policy forbids some uses / heavy saturation | M | M | template-only, policy-check item, multi-niche | Rockstar policy text [U] |
| R13 | SQLite write contention with multiple workers | M | M | WAL, short transactions, single writer queue; Postgres for multi-worker | Measure in P1 |
| R14 | Third-party API app review (YouTube/TikTok) blocks publishing | H | M | manual export workflow is the primary path | — |
| R15 | Maintainer bandwidth for OSS | M | H | ADRs, good-first-issues, CI strictness, small surface | — |

---

## 12. Delivery Roadmap (milestones & acceptance criteria)

**M0 — Prototypes / spikes (de-risking, throwaway code in `spikes/`, ~1 wk)**
- P1: faster-whisper CPU/GPU benchmark on 3 reference files; queue (custom vs Huey) under SQLite. *AC: measured table in docs/benchmarks.md.*
- P2: dynamic crop path rendering in FFmpeg (sendcmd vs PyAV pipe), quality + speed. *AC: 60 s smooth 9:16 crop rendered ≤ 2× realtime on CPU.*
- P3: face-track smoothing; active-speaker heuristic feasibility. *AC: go/no-go for v1.0 auto speaker framing.*
- P4: heuristic highlight scorer vs. random/uniform baseline on labelled set. *AC: precision@5 measurably above baseline.*
- P5: ASS caption render vs browser preview parity. *AC: position error ≤ 2% of frame.*

**M1 — Foundation:** repo scaffolding, config, DB+migrations, job queue w/ cancel/retry/recovery, storage, CLI `doctor`, CI (lint/type/test on 3 OSes). *AC: kill-worker test passes; CI green.*
**M2 — Import & media:** upload, validation, probe, proxy, waveform, rights declaration, project dashboard. *AC: corrupted-corpus tests produce correct error codes; 3 h file imports with bounded RAM.*
**M3 — Analysis:** ASR, scenes, silence, faces, scoring, clip suggestions with reasons, optional Ollama. *AC: E2E analyze on PD clip yields ≥3 sentence-bounded clips.*
**M4 — Editor & captions:** gallery, trim, transcript edit, caption presets, framing override, preview. *AC: edits persist with optimistic concurrency; Playwright flows pass.*
**M5 — Render & export (MVP complete):** filter-graph compiler, presets, loudnorm, SRT/VTT, export verification, job history. *AC: golden tests; full video→export E2E on all 3 OS CI runners.* → **tag v0.1.0 (Prototype/Beta per honest assessment)**
**M6 — v1.0 features:** batch, diarization opt-in, layouts/split-screen, brand kit, voiceover + ducking, annotations, templates incl. gaming, checklist, multi-user mode, Postgres, Docker prod compose. 
**M7 — Hardening:** security tests, fuzzing, perf matrix, clean-install VMs, docs audit, licence audit, SBOM, signed release. *AC: all release-critical items in §13 verified* → **v1.0.0**

---

## 13. Final Plan Validation (self-review; issues found and how the plan was revised)
1. **Contradiction: "publish through supported workflows" & "track performance" vs. no third-party APIs in MVP.** Resolved: publishing in MVP/v1.0 = export + per-platform metadata/checklist + open-folder; API publishing/analytics moved to Future with app-review dependency stated (R14).
2. **"Speaker tracking" vs. honest feasibility.** Moved auto active-speaker to v1.0 behind prototype P3 go/no-go; MVP uses dominant/largest face + manual override.
3. **Celery initially considered** — contradicts "easy local install on Windows." Replaced with DB-backed queue (Huey as evaluated alternative).
4. **WhisperX as default** would pull PyTorch + pyannote + churn (yanked releases [V]). Revised: faster-whisper default, WhisperX/pyannote optional.
5. **AGPL/non-commercial model contamination** — YOLO (Ultralytics) and InsightFace explicitly excluded.
6. **FFmpeg GPL** — resolved by not bundling binaries in repo; documented in NOTICE for Docker image.
7. **Sample media licensing** — no copyrighted samples; synthetic + public-domain with provenance.
8. **Monetization** — only an advisory checklist; voiceover/annotation features prioritized because permission alone is insufficient for YouTube monetization [V].
9. **Local server security gap** (DNS rebinding) — added Host/Origin checks + per-install token.
10. **Unrealistic: "production-ready for thousands of users" in MVP** — MVP is single-user local; multi-user is v1.0; scaling path documented, not promised.
11. **Missing: model download/offline story** — added checksum-verified first-use download + offline docs.
12. **Missing: preview/export parity** — added P5 + golden tests.

---

## 14. Readiness Assessment
**Verified (this pass, 2026-11-15):** GTA VI date/platforms/no PC date; YouTube reused-content principle & July 2026 restructure (via third-party summaries — official page re-read required); faster-whisper 1.2.1 stable; WhisperX 3.8.6 requirements & yanked versions.
**From established knowledge, not re-verified this pass:** licences of FastAPI/React/PySceneDetect/MediaPipe/OpenCV/CTranslate2/libass/FFmpeg; Whisper weight licence; LibriVox PD status. → re-verify with links in `docs/models-and-licenses.md` at M1.
**Not verified / unknown:** Rockstar's current video policy text (fetch failed); pyannote community-1 licence; console capture limits; actual processing times on target hardware; winget FFmpeg package id; Ollama default model choice & licence.
**Needs prototype:** P1–P5 (§12 M0).
**Depends on external services:** none for core. Optional: Ollama (local), OpenAI-compatible endpoint, Hugging Face (diarization model download), future YouTube/TikTok/Instagram APIs (app review).
**Could block production release:** poor highlight quality (R1); unacceptable CPU performance (R2); Windows install reliability (R5); unresolved model licence (R9); failing clean-install or recovery tests.

**Decisions needed from you before Phase 2:**
1. Project license: **Apache-2.0 (recommended)** or AGPL-3.0?
2. Confirm MVP scope = single-user local; multi-user/self-host in v1.0.
3. Confirm no URL/platform downloader (only local files in MVP).
4. Product name (default "Clipper" — likely trademark-crowded; suggest choosing a distinct name).
5. Whether Phase 2 should start with the M0 prototypes (recommended) or go straight to M1.

---

## 15. Revisions after M0 prototypes (2026-11-15) — see `docs/benchmarks.md`
- **Approved:** licence Apache-2.0; single-user local MVP; no downloader; working name "Clipper" (to be renamed before release).
- §4.3: FFmpeg **8.1.x** is the current Windows build; minimum supported 7.0.
- §4.4: pin `av>=17,<18` with faster-whisper 1.2.1; GPU support ships as optional `[gpu]` extra (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) with Windows DLL-path registration; `clipper doctor` reports CUDA status.
- §4.7: prefer **OpenCV YuNet** only; MediaPipe 1.2.x dropped unless YuNet fails accuracy tests (MediaPipe removed legacy API and duplicates OpenCV).
- §4.11: **Huey rejected** (task lost on worker crash). Custom lease queue confirmed by test.
- §4.3/P2: crop paths rendered with FFmpeg `sendcmd`.
- §4.9: caption engine must do line-breaking to frame width, and preview must convert ASS size → CSS size via font metrics.
- §4.6: add `topic_break` (long pause) penalty; filler detection must not match "did you know".
- Still open: real-footage validation of face detection, active speaker, ASR on real/gaming audio, highlight quality.

## 16. Revisions during M4 (2026-11-16)
- Frontend package manager is **npm** (ships with Node; one fewer tool for contributors) instead of pnpm.
- UI components are plain Tailwind + small local components instead of shadcn/ui (fewer dependencies; can be
  revisited).
- Per-install API token added (header or `SameSite=Strict` cookie) on top of Host/Origin checks.
- Caption line-breaking is server-side only; the preview draws server-provided groups. Preview font metrics are
  approximate until the renderer calibrates them in M5.
- Playwright runs against installed Edge/Chrome channels (no browser download).
