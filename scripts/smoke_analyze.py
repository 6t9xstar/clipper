"""Smoke test of the whole M3 path against a real `clipper serve` process (no test doubles).

Builds a ~2 minute talk video from OS text-to-speech (Windows SAPI only) over generated visuals, uploads it,
runs the analysis with real Whisper and prints the suggested clips. Nothing is downloaded except the Whisper
model on first use.

Usage: uv run python scripts/smoke_analyze.py [--model tiny] [--data-dir data/smoke] [--port 8799]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

PARAGRAPHS = [
    "So um anyway, I mean, that was kind of the thing, you know. We went there and it was fine I guess.",
    "Why do most new runners get injured in the first three weeks? Here's the mistake. They increase their "
    "distance by thirty percent every single week. The rule that works is ten percent per week. That one "
    "change cut injuries in half in our running club last year.",
    "And then they said something about it, but I don't really remember. So yeah, that was that.",
    "Did you know that sleep matters more than any shoe you can buy? Runners who sleep eight hours recover "
    "about twice as fast as runners who sleep six. If you remember one thing today, protect your sleep.",
    "Okay, so, where was I. Anyway, um, let's see. I mean, it's whatever, we can come back to it.",
]


def build_video(out: Path, tmp: Path) -> None:
    if sys.platform != "win32":
        sys.exit("this smoke script uses Windows SAPI text-to-speech")
    wavs = []
    for i, text in enumerate(PARAGRAPHS):
        w = tmp / f"p{i}.wav"
        quoted = text.replace("'", "''")  # PowerShell single-quote escaping
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.SetOutputToWaveFile('{w}'); $s.Speak('{quoted}'); $s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=120)
        wavs.append(w)
    lst = tmp / "list.txt"
    sil = tmp / "sil.wav"
    ff = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run(
        [
            ff,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=22050:cl=mono",
            "-t",
            "2",
            "-c:a",
            "pcm_s16le",
            str(sil),
        ],
        check=True,
    )
    lst.write_text("".join(f"file '{w.as_posix()}'\nfile '{sil.as_posix()}'\n" for w in wavs))
    speech = tmp / "speech.wav"
    subprocess.run(
        [
            ff,
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-ar",
            "22050",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(speech),
        ],
        check=True,
    )
    subprocess.run(
        [
            ff,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=1280x720:r=30",
            "-i",
            str(speech),
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(out),
        ],
        check=True,
    )


def wait_job(c: httpx.Client, job_id: str, timeout: float = 1800) -> dict[str, object]:
    end = time.time() + timeout
    last = None
    while time.time() < end:
        j: dict[str, object] = c.get(f"/api/v1/jobs/{job_id}").json()
        msg = (j["status"], j.get("progress_message"))
        if msg != last:
            print(f"  job {j['type']}: {j['status']} {j.get('progress_message') or ''} {j['progress']:.0%}")
            last = msg
        if j["status"] in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(1)
    raise TimeoutError(job_id)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="tiny")
    ap.add_argument("--data-dir", type=Path, default=Path("data/smoke"))
    ap.add_argument("--port", type=int, default=8799)
    a = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        video = tmp / "talk.mp4"
        build_video(video, tmp)
        env = {
            **os.environ,
            "CLIPPER_DATA_DIR": str(a.data_dir),
            "CLIPPER_PORT": str(a.port),
            "CLIPPER_ASR_MODEL": a.model,
            "CLIPPER_LOG_LEVEL": "WARNING",
        }
        server = subprocess.Popen([sys.executable, "-m", "clipper.cli", "serve"], env=env)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{a.port}", timeout=120) as c:
                for _ in range(120):
                    try:
                        if c.get("/healthz").status_code == 200:
                            break
                    except httpx.HTTPError:
                        time.sleep(0.5)
                pid = c.post("/api/v1/projects", json={"name": "smoke"}).json()["id"]
                data = video.read_bytes()
                body = {
                    "filename": "talk.mp4",
                    "size_bytes": len(data),
                    "rights_type": "own_content",
                    "rights_confirmed": True,
                }
                asset = c.post(f"/api/v1/projects/{pid}/assets", json=body).json()
                asset = c.put(asset["upload_url"], content=data, headers={"upload-offset": "0"}).json()
                print("import:")
                wait_job(c, asset["import_job_id"])
                t0 = time.time()
                print("analyze:")
                job = c.post(
                    f"/api/v1/assets/{asset['id']}/analyze",
                    json={"min_seconds": 8, "max_seconds": 30, "count": 3},
                ).json()
                j = wait_job(c, job["id"])
                print(f"analysis took {time.time() - t0:.1f} s -> {j['result'] or j.get('error_code')}")
                tr = c.get(f"/api/v1/assets/{asset['id']}/transcript").json()
                print(f"transcript: {tr['word_count']} words, model={tr['model']} device={tr['device']}")
                for clip in c.get(f"/api/v1/assets/{asset['id']}/clips").json()["items"]:
                    print(
                        f"#{clip['rank']} {clip['start_ms'] / 1000:6.2f}-{clip['end_ms'] / 1000:6.2f}s "
                        f"score={clip['score']:.2f} {clip['reasons']}\n    {clip['title']}"
                    )
        finally:
            server.terminate()
            server.wait(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
