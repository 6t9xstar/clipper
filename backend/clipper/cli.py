"""`clipper` command line: serve | worker | migrate | doctor."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any

from clipper import __version__
from clipper.core.config import get_settings


def _ffmpeg_version(exe: str) -> str | None:
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.splitlines()[0] if out else None


def doctor() -> dict[str, Any]:
    """Environment report. Each check has status ok|warn|fail and a fix hint."""
    s = get_settings()
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str, fix: str = "") -> None:
        checks.append({"check": name, "status": status, "detail": detail, "fix": fix})

    py = sys.version_info
    add(
        "python",
        "ok" if (3, 12) <= py[:2] < (3, 14) else "fail",
        platform.python_version(),
        "Install Python 3.12 or 3.13 (uv does this automatically: `uv sync`).",
    )
    for tool in ("ffmpeg", "ffprobe"):
        exe = shutil.which(tool)
        ver = _ffmpeg_version(exe) if exe else None
        add(
            tool,
            "ok" if ver else "fail",
            ver or "not found",
            "Windows: `winget install Gyan.FFmpeg`; macOS: `brew install ffmpeg`; "
            "Debian/Ubuntu: `apt install ffmpeg`.",
        )
    ff = shutil.which("ffmpeg")
    if ff:
        try:
            enc = subprocess.run(
                [ff, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30
            ).stdout
            filt = subprocess.run(
                [ff, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            enc = filt = ""
        missing = [n for n in ("libx264", "aac") if f" {n} " not in enc]
        missing += [f"filter:{n}" for n in ("tile", "scale", "ass") if f" {n} " not in filt]
        add("ffmpeg_features", "fail" if missing else "ok",
            "missing: " + ", ".join(missing) if missing else "libx264, aac, tile, scale, ass available",
            "Install a full FFmpeg build (e.g. Gyan.FFmpeg on Windows, distro ffmpeg on Linux).")  # fmt: skip
    try:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        probe = s.data_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        free = shutil.disk_usage(s.data_dir).free / 1024**3
        add(
            "data_dir",
            "ok" if free > 10 else "warn",
            f"{s.data_dir} ({free:.1f} GB free)",
            "At least 10 GB free is recommended; set CLIPPER_DATA_DIR to a larger drive.",
        )
    except OSError as e:
        add("data_dir", "fail", f"{s.data_dir}: {e}", "Set CLIPPER_DATA_DIR to a writable folder.")
    try:
        from clipper.db import migrate
        from clipper.db.session import Database

        db = Database(s.db_url)
        rev = migrate.current_revision(db)
        db.dispose()
        if rev:
            add("database", "ok", f"revision {rev}")
        else:  # fresh install: serve/worker create it automatically
            add("database", "ok", "not created yet - `clipper serve` creates it on first start")
    except Exception as e:  # report, don't crash, in a diagnostics command
        add("database", "fail", f"{type(e).__name__}: {e}", "Check CLIPPER_DATABASE_URL.")
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi:
        try:
            gpu = subprocess.run(
                [nvsmi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout.strip()
            add("gpu", "ok", gpu or "NVIDIA driver present, no GPU listed")
        except (OSError, subprocess.TimeoutExpired):
            add("gpu", "warn", "nvidia-smi failed", "GPU acceleration is optional; CPU works.")
    else:
        add("gpu", "ok", "no NVIDIA GPU detected - CPU mode (supported)")
    if s.host not in ("127.0.0.1", "localhost", "::1"):
        add(
            "bind",
            "warn",
            f"listening on {s.host}",
            "Multi-user auth arrives in v1.0. Until then expose only behind an authenticating reverse proxy.",
        )
    worst = (
        "fail"
        if any(c["status"] == "fail" for c in checks)
        else ("warn" if any(c["status"] == "warn" for c in checks) else "ok")
    )
    return {"version": __version__, "status": worst, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clipper", description="Local-first AI video clipper")
    ap.add_argument("--version", action="version", version=f"clipper {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_serve = sub.add_parser("serve", help="run API server (and an embedded worker)")
    p_serve.add_argument("--no-worker", action="store_true", help="don't start the embedded worker")
    sub.add_parser("worker", help="run a standalone background worker")
    sub.add_parser("migrate", help="apply database migrations")
    p_doc = sub.add_parser("doctor", help="check the environment")
    p_doc.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    s = get_settings()

    if a.cmd == "doctor":
        rep = doctor()
        if a.json:
            print(json.dumps(rep, indent=2))
        else:
            for c in rep["checks"]:
                mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[c["status"]]
                print(f"[{mark}] {c['check']:<9} {c['detail']}")
                if c["status"] != "ok" and c["fix"]:
                    print(f"         fix: {c['fix']}")
            print(f"\noverall: {rep['status'].upper()}")
        return 1 if rep["status"] == "fail" else 0

    from clipper.core.logging import configure_logging
    from clipper.db import migrate

    configure_logging(s.log_level, s.log_json)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    if a.cmd == "migrate":
        migrate.upgrade(s.db_url)
        print("database is up to date")
        return 0

    if a.cmd == "worker":
        migrate.upgrade(s.db_url)
        return _run_workers(s.worker_concurrency, block=True)

    if a.cmd == "serve":
        import uvicorn

        from clipper.api.app import create_app

        migrate.upgrade(s.db_url)
        if not a.no_worker:
            _run_workers(s.worker_concurrency, block=False)
        uvicorn.run(create_app(s), host=s.host, port=s.port, log_config=None, access_log=False)
        return 0
    return 2


def _run_workers(n: int, *, block: bool) -> int:
    import clipper.ai.analyze
    import clipper.jobs.builtin
    import clipper.media.importer  # noqa: F401
    from clipper.core.services import Services
    from clipper.jobs.worker import Worker
    from clipper.maintenance import sweep

    s = get_settings()
    svc = Services(s)
    sweep(svc)
    workers = [Worker(svc.queue, poll_seconds=s.worker_poll_seconds, services=svc) for _ in range(n)]
    threads = [
        threading.Thread(target=w.run_forever, daemon=not block, name=f"worker-{i}")
        for i, w in enumerate(workers)
    ]

    def _housekeeping() -> None:
        while True:
            time.sleep(3600)
            try:
                sweep(svc)
            except Exception:  # never let housekeeping kill the process
                from clipper.core.logging import log

                log.warning("maintenance.failed", exc_info=True)

    threading.Thread(target=_housekeeping, daemon=True, name="housekeeping").start()
    for t in threads:
        t.start()
    if block:

        def _stop(*_: object) -> None:
            for w in workers:
                w.stop()

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        for t in threads:
            while t.is_alive():
                t.join(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
