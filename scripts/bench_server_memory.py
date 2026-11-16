"""Measure memory of a real `clipper serve` process (API + embedded worker) while it receives and imports
a file.

Usage: uv run python scripts/bench_server_memory.py <video> [--data-dir data/bench_srv] [--port 8798]
The client runs in THIS process and streams chunks from disk; only the server's process tree is measured.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--data-dir", type=Path, default=Path("data/bench_srv"))
    ap.add_argument("--port", type=int, default=8798)
    ap.add_argument("--chunk-mb", type=int, default=64)
    a = ap.parse_args()
    env = {**os.environ, "CLIPPER_DATA_DIR": str(a.data_dir.resolve()), "CLIPPER_PORT": str(a.port),
           "CLIPPER_LOG_LEVEL": "WARNING"}  # fmt: skip
    server = subprocess.Popen([sys.executable, "-m", "clipper.cli", "serve"], env=env)
    sp = psutil.Process(server.pid)
    peaks = {"upload": [0.0, 0.0], "import": [0.0, 0.0]}  # [server python MB, server tree MB]
    phase = ["upload"]
    stop = threading.Event()

    def python_rss() -> int:
        # On Windows the venv's python.exe is a small launcher that starts the real interpreter as a child,
        # so count every python process in the tree (the M2 run reported the 5 MB launcher by mistake).
        procs = [sp, *sp.children(recursive=True)]
        return sum(p.memory_info().rss for p in procs if p.name().lower().startswith("python"))

    def sample() -> None:
        while not stop.is_set():
            try:
                py = python_rss()
                tree = sp.memory_info().rss + sum(c.memory_info().rss for c in sp.children(recursive=True))
            except psutil.Error:
                stop.wait(0.2)
                continue
            p = peaks[phase[0]]
            p[0], p[1] = max(p[0], py / 2**20), max(p[1], tree / 2**20)
            stop.wait(0.2)

    base = f"http://127.0.0.1:{a.port}"
    try:
        with httpx.Client(base_url=base, timeout=600) as c:
            for _ in range(120):
                try:
                    if c.get("/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.5)
            # API calls need the per-install token, which the server wrote on start-up
            c.headers["x-clipper-token"] = (a.data_dir.resolve() / "api_token").read_text().strip()
            idle = python_rss() / 2**20
            threading.Thread(target=sample, daemon=True).start()
            pid = c.post("/api/v1/projects", json={"name": "srv-bench"}).json()["id"]
            size = a.video.stat().st_size
            body = {"filename": a.video.name, "size_bytes": size, "rights_type": "own_content",
                    "rights_confirmed": True}  # fmt: skip
            asset = c.post(f"/api/v1/projects/{pid}/assets", json=body).json()
            t0, off, chunk = time.time(), 0, a.chunk_mb * 2**20
            with a.video.open("rb") as fh:
                while off < size:
                    r = c.put(
                        asset["upload_url"], content=fh.read(chunk), headers={"upload-offset": str(off)}
                    )
                    r.raise_for_status()
                    off = r.json()["received_bytes"]
            t_up = time.time() - t0
            phase[0] = "import"
            t0 = time.time()
            while True:
                got = c.get(f"/api/v1/assets/{asset['id']}").json()
                if got["status"] in ("ready", "failed"):
                    break
                time.sleep(2)
            t_imp = time.time() - t0
    finally:
        stop.set()
        server.terminate()
        try:
            server.wait(20)
        except subprocess.TimeoutExpired:
            server.kill()
    print(json.dumps({
        "file_mb": round(size / 1e6), "status": got["status"], "error": got["error_code"],
        "server_idle_rss_mb": round(idle), "upload_s": round(t_up, 1), "import_s": round(t_imp, 1),
        "peak_server_python_mb": {k: round(v[0]) for k, v in peaks.items()},
        "peak_server_tree_mb": {k: round(v[1]) for k, v in peaks.items()},
    }, indent=1))  # fmt: skip


if __name__ == "__main__":
    main()
