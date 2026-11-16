# Contributing

Thanks for helping! Please read [PHASE1_PLAN.md](docs/PHASE1_PLAN.md) for scope and architecture first.

## Setup
1. Install [uv](https://docs.astral.sh/uv/) and FFmpeg 7+.
2. `uv sync` then `uv run clipper doctor`.
3. For the web UI: Node.js 22+ and, in `frontend/`, `npm ci`.

## Before opening a pull request
```bash
uv run ruff format backend && uv run ruff check backend
uv run mypy
uv run pytest -q
cd frontend && npm run format && npm run lint && npm run typecheck && npm test && npm run build
npm run e2e   # Playwright; uses installed Edge (set CLIPPER_E2E_CHANNEL=chrome for Chrome)
```
CI runs the Python checks on Linux, Windows and macOS with Python 3.12 and 3.13, and the web checks plus the
Playwright flows on Linux with Chrome.

## Rules
- **Schema changes need an Alembic migration** in `backend/clipper/migrations/versions/` (a test enforces it).
- **New settings** must be documented in `.env.example` and `docs/configuration.md` (a test enforces it).
- Never log transcript text, user file names or media content — log IDs.
- Run external tools via `clipper.jobs.proc.run` with argv lists — never `shell=True`.
- Every FFmpeg/ffprobe call that reads a user file must include `clipper.media.probe.SAFE_INPUT_ARGS`.
- No copyrighted test media. Generate test media with FFmpeg or use public-domain/CC0 files with provenance.
- No features that download from platforms against their terms, or that evade copyright detection.
- Every user-visible error needs a stable `code` and a helpful `hint`.
- UI: never inject HTML (`dangerouslySetInnerHTML` is banned by lint); no inline `<script>`/`<style>` (the CSP blocks them).
- Caption grouping lives only in the backend; framing maths changes must update both twins and `testdata/framing_cases.json`.

## Commit messages
Short imperative subject (`Add job retry endpoint`), body explaining *why*.
