"""Benchmark: import a long video through the real API + worker and record time and peak memory.

Usage:  uv run python scripts/bench_long_import.py --hours 3 --data-dir data/bench
Generates a synthetic video (FFmpeg test pattern + tone/noise; no third-party media) unless it already exists.
Peak memory is sampled every 0.2 s for this Python process and all child processes (FFmpeg).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from pathlib import Path

import psutil
from fastapi.testclient import TestClient

import clipper.media.importer  # noqa: F401
from clipper.api.app import create_app
from clipper.core.config import Settings
from clipper.jobs.worker import Worker


def generate(path: Path, hours: float) -> None:
    secs = int(hours * 3600)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=30:d={secs}",
         "-f", "lavfi", "-i", f"sine=f=220:sample_rate=48000:d={secs}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-g", "300",
         "-c:a", "aac", "-b:a", "64k", "-shortest", "-movflags", "+faststart", str(path)],
        check=True,
    )  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--data-dir", type=Path, default=Path("data/bench"))
    a = ap.parse_args()
    a.data_dir.mkdir(parents=True, exist_ok=True)
    src = a.data_dir / f"long_{a.hours:g}h.mp4"
    if not src.exists():
        t0 = time.time()
        generate(src, a.hours)
        print(f"generated {src.stat().st_size / 1e6:.0f} MB in {time.time() - t0:.0f}s", flush=True)

    me = psutil.Process()
    peaks = {"python_rss_mb": 0.0, "children_rss_mb": 0.0, "total_rss_mb": 0.0}
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            try:
                py = me.memory_info().rss
                ch = sum(c.memory_info().rss for c in me.children(recursive=True) if c.is_running())
            except psutil.Error:
                continue
            peaks["python_rss_mb"] = max(peaks["python_rss_mb"], py / 2**20)
            peaks["children_rss_mb"] = max(peaks["children_rss_mb"], ch / 2**20)
            peaks["total_rss_mb"] = max(peaks["total_rss_mb"], (py + ch) / 2**20)
            stop.wait(0.2)

    threading.Thread(target=sample, daemon=True).start()
    settings = Settings(data_dir=a.data_dir / "app", log_json=True, log_level="WARNING")
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as c:
        svc = c.app.state.clipper  # type: ignore[attr-defined]
        pid = c.post("/api/v1/projects", json={"name": "bench"}).json()["id"]
        size = src.stat().st_size
        asset = c.post(f"/api/v1/projects/{pid}/assets", json={
            "filename": src.name, "size_bytes": size, "rights_type": "own_content", "rights_confirmed": True,
        }).json()  # fmt: skip
        t0 = time.time()
        off, chunk = 0, 64 * 1024 * 1024
        with src.open("rb") as fh:
            while off < size:
                r = c.put(asset["upload_url"], content=fh.read(chunk), headers={"upload-offset": str(off)})
                off = r.json()["received_bytes"]
        t_upload = time.time() - t0
        upload_peak = dict(peaks)
        t0 = time.time()
        w = Worker(svc.queue, services=svc, worker_id="bench")
        while w.run_once():
            pass
        t_import = time.time() - t0
        got = c.get(f"/api/v1/assets/{asset['id']}").json()
    stop.set()
    print(json.dumps({
        "hours": a.hours, "file_mb": round(size / 1e6), "status": got["status"], "error": got["error_code"],
        "duration_ms": got["duration_ms"], "upload_s": round(t_upload, 1), "import_s": round(t_import, 1),
        "import_realtime_x": round(a.hours * 3600 / t_import, 1),
        "peak_during_upload": {k: round(v) for k, v in upload_peak.items()},
        "peak_overall": {k: round(v) for k, v in peaks.items()},
        "thumbs": (got["media_info"] or {}).get("thumbs"),
    }, indent=1))  # fmt: skip


if __name__ == "__main__":
    main()
