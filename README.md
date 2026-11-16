# Clipper

**Open-source, local-first AI video clipper** that turns your long videos into short vertical clips with captions —
on your own computer, without uploading your media anywhere.

> **Status: pre-alpha (milestone M4 of 7).** Done and tested: backend foundation (M1), video import (M2: upload,
> validation, preview proxy, thumbnails, waveform), AI analysis (M3: local speech-to-text, shot-cut detection,
> explainable clip suggestions) and a local web editor (M4: clip gallery, trimming, transcript corrections,
> caption styles, 9:16 framing with a live preview). **Rendering/exporting the finished vertical video is not in
> the app yet** (M5); the preview only approximates the final caption fonts. Do not use this for real work yet.

Use it only with videos you made or have permission to use. See the plan's legal section
([docs/PHASE1_PLAN.md §3](docs/PHASE1_PLAN.md)). Clipper never promises views or monetization.

## Requirements
- Windows 10/11, macOS 13+, or Linux (x86-64 or ARM64)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (installs the right Python automatically)
- FFmpeg 7+ on your PATH
  - Windows: `winget install Gyan.FFmpeg` · macOS: `brew install ffmpeg` · Debian/Ubuntu: `sudo apt install ffmpeg`
- Optional: an NVIDIA GPU makes AI steps faster (CPU works)

## Quick start (development)
```bash
git clone <repo-url> clipper && cd clipper
uv sync                 # creates .venv with locked dependencies
uv run clipper doctor   # checks Python, FFmpeg, disk, database, GPU
cd frontend && npm ci && npm run build && cd ..   # builds the web UI (needs Node.js 22+)
uv run clipper serve    # web UI + API + background worker on http://127.0.0.1:8765
```
Open http://127.0.0.1:8765 in your browser. API docs: http://127.0.0.1:8765/api/docs · Health: `/healthz` ·
Readiness: `/readyz`

The API only answers this computer and requires the per-install access token stored in `data/api_token`
(the web UI receives it automatically as a cookie). Run a self-test job (exercises the queue and FFmpeg):
```bash
curl -X POST http://127.0.0.1:8765/api/v1/jobs -H "content-type: application/json" -H "x-clipper-token: $(cat data/api_token)" -d '{"type":"system.selftest"}'
```

UI development with hot reload: run `uv run clipper serve` and, in `frontend/`, `npm run dev` (Vite proxies
`/api` to the server and adds the token).

## Commands
| Command | What it does |
|---|---|
| `clipper serve [--no-worker]` | Runs the API (applies migrations first) and, unless disabled, a background worker |
| `clipper worker` | Runs a standalone worker (for separate processes/machines sharing the DB) |
| `clipper migrate` | Applies database migrations |
| `clipper doctor [--json]` | Diagnoses the environment and prints fixes |

Configuration: [docs/configuration.md](docs/configuration.md) (copy `.env.example` to `.env`).

## Development checks
```bash
uv run ruff check backend && uv run ruff format --check backend
uv run mypy
uv run pytest -q
cd frontend && npm run lint && npm run typecheck && npm test && npm run build
npm run e2e             # Playwright against a real server; uses your installed Edge (CLIPPER_E2E_CHANNEL=chrome for Chrome)
```

## Documentation
- [Phase 1 plan & architecture](docs/PHASE1_PLAN.md) · [Prototype results](docs/benchmarks.md)
- [Architecture (implemented)](docs/architecture.md) · [API guide](docs/api.md) · [Configuration](docs/configuration.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)

## License
Apache-2.0 — see [LICENSE](LICENSE). Third-party notices: [NOTICE](NOTICE).
