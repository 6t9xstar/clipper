"""End-to-end analysis: real API, real worker, real FFmpeg scene detection, real scorer.

Speech recognition is replaced by a test double (a fixed word list) so the test is fast, deterministic and
needs no model download; the real faster-whisper path is covered by test_asr_real.py.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import clipper.ai.analyze  # noqa: F401  (registers handler)
from clipper.ai import asr
from clipper.api.app import create_app
from clipper.core.config import Settings
from clipper.media.importer import asset_prefix

from . import media_corpus
from .test_highlights import STRONG, WEAK, speak
from .test_import import create, drain, import_file, pytestmark, svc  # noqa: F401  (pytestmark: needs ffmpeg)
from .util import authed

CUTS = (20.0, 35.0)
DURATION = 50.0


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """50 s: red 0-20 s, white 20-35 s, blue 35-50 s (two hard cuts) with a tone."""
    out = tmp_path_factory.mktemp("analyze") / "talk.mp4"
    col = "color=c={}:s=320x240:r=30:d={}"
    media_corpus._ff(
        "-f", "lavfi", "-i", col.format("red", 20), "-f", "lavfi", "-i", col.format("white", 15),
        "-f", "lavfi", "-i", col.format("blue", 15), "-f", "lavfi", "-i", f"sine=f=300:d={DURATION}",
        "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out),
    )  # fmt: skip
    return out


def fake_words() -> list[dict[str, Any]]:
    a, t = speak(STRONG, 1.0)
    b, _ = speak(WEAK, t + 2.5)
    return a + b


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    n: list[int] = []

    def fake(audio: Path, **kw: Any) -> asr.TranscriptResult:
        assert audio.name == "audio.wav" and audio.exists()
        n.append(1)
        ws = [asr.Word(w["w"], w["s"], w["e"], w["p"]) for w in fake_words()]
        return asr.TranscriptResult("en", 0.99, DURATION, "tiny", "cpu", ws, ["test double"])

    monkeypatch.setattr(asr, "transcribe", fake)
    return n


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    st = settings.model_copy(update={"asr_model": "tiny", "asr_device": "cpu"})
    with authed(create_app(st)) as c:
        yield c


def analyze(c: TestClient, aid: str, **body: Any) -> dict[str, Any]:
    r = c.post(
        f"/api/v1/assets/{aid}/analyze", json={"min_seconds": 5, "max_seconds": 25, "count": 3, **body}
    )
    assert r.status_code == 202, r.json()
    drain(c)
    job: dict[str, Any] = c.get(f"/api/v1/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job
    return job


def clips(c: TestClient, aid: str, **q: str) -> list[dict[str, Any]]:
    r = c.get(f"/api/v1/assets/{aid}/clips", params=q)
    assert r.status_code == 200, r.json()
    items: list[dict[str, Any]] = r.json()["items"]
    return items


def test_analyze_end_to_end(client: TestClient, video: Path, calls: list[int]) -> None:
    a = import_file(client, video)
    assert a["status"] == "ready", a
    aid = a["id"]
    assert client.get(f"/api/v1/assets/{aid}/transcript").json()["code"] == "TRANSCRIPT_NOT_FOUND"

    job = analyze(client, aid)
    res = job["result"]
    assert res["mode"] == "speech" and res["language"] == "en" and res["cuts"] == 2 and res["suggested"] >= 1
    assert calls == [1]

    # shot cuts detected by real FFmpeg scdet, cached
    a = client.get(f"/api/v1/assets/{aid}").json()
    derived = svc(client).storage.path(f"{asset_prefix(a['project_id'], aid)}/derived")
    cuts = json.loads((derived / "scenes.json").read_text())["cuts"]
    assert len(cuts) == 2 and all(abs(x - y) <= 0.2 for x, y in zip(cuts, CUTS, strict=True))
    assert a["analysis_job_id"] == job["id"]

    # transcript endpoint
    tr = client.get(f"/api/v1/assets/{aid}/transcript").json()
    assert tr["model"] == "tiny" and tr["word_count"] == len(fake_words()) == len(tr["words"])

    # suggestions: best first, inside the video, explained
    cs = clips(client, aid)
    assert [c["rank"] for c in cs] == list(range(1, len(cs) + 1))
    top = cs[0]
    assert top["title"].startswith("Why do most new runners") and top["reasons"]
    assert all(0 <= c["start_ms"] < c["end_ms"] <= a["duration_ms"] for c in cs)
    assert all(c["origin"] == "ai" and c["status"] == "suggested" for c in cs)


def test_clip_editing_and_reanalysis(client: TestClient, video: Path, calls: list[int]) -> None:
    aid = import_file(client, video)["id"]
    analyze(client, aid)
    top = clips(client, aid)[0]
    url = f"/api/v1/clips/{top['id']}"

    r = client.get(url)
    assert r.headers["etag"] == '"1"'
    assert client.patch(url, json={"status": "approved"}).json()["code"] == "PRECONDITION_REQUIRED"
    r = client.patch(
        url, json={"status": "approved", "end_ms": top["end_ms"] - 500}, headers={"if-match": '"1"'}
    )
    assert r.status_code == 200 and r.json()["version"] == 2 and r.headers["etag"] == '"2"'
    stale = client.patch(url, json={"title": "x"}, headers={"if-match": '"1"'})
    assert (
        stale.status_code == 412
        and stale.json()["code"] == "VERSION_CONFLICT"
        and stale.json()["version"] == 2
    )
    bad = client.patch(url, json={"end_ms": 10**9}, headers={"if-match": '"2"'})
    assert bad.status_code == 422 and bad.json()["code"] == "CLIP_OUT_OF_RANGE"
    short = client.patch(url, json={"end_ms": top["start_ms"] + 200}, headers={"if-match": '"2"'})
    assert short.json()["code"] == "CLIP_LENGTH"

    # re-analysis: cached transcript (no second ASR run), approved clip kept, suggestions replaced
    old = {c["id"] for c in clips(client, aid, status="suggested")}
    job = analyze(client, aid, count=5)
    assert calls == [1], "transcript should come from cache"
    assert client.get(url).json()["status"] == "approved"
    new = clips(client, aid, status="suggested")
    assert not old & {c["id"] for c in new}
    t0, t1 = top["start_ms"], top["end_ms"] - 500
    for c in new:
        inter = max(0, min(t1, c["end_ms"]) - max(t0, c["start_ms"]))
        assert inter <= 0.5 * min(t1 - t0, c["end_ms"] - c["start_ms"])
    assert job["result"]["suggested"] == len(new)

    # manual clip
    r = client.post(f"/api/v1/assets/{aid}/clips", json={"start_ms": 1000, "end_ms": 9000, "title": "Mine"})
    assert r.status_code == 201 and r.json()["origin"] == "manual" and r.headers["etag"] == '"1"'
    assert (
        client.post(f"/api/v1/assets/{aid}/clips", json={"start_ms": 5000, "end_ms": 4000}).status_code == 422
    )
    mid = r.json()["id"]
    assert client.delete(f"/api/v1/clips/{mid}").status_code == 204
    assert client.get(f"/api/v1/clips/{mid}").json()["code"] == "CLIP_NOT_FOUND"

    # deleting the video removes its clips
    assert client.delete(f"/api/v1/assets/{aid}").status_code == 204
    assert client.get(url).status_code == 404


def test_analyze_rejections(client: TestClient, video: Path, calls: list[int]) -> None:
    pid = client.post("/api/v1/projects", json={"name": "P"}).json()["id"]
    pending = create(client, pid, "later.mp4", 1000).json()
    r = client.post(f"/api/v1/assets/{pending['id']}/analyze", json={})
    assert r.status_code == 409 and r.json()["code"] == "ASSET_NOT_READY"

    aid = import_file(client, video)["id"]
    r = client.post(f"/api/v1/assets/{aid}/analyze", json={"min_seconds": 30, "max_seconds": 20})
    assert r.status_code == 422
    first = client.post(f"/api/v1/assets/{aid}/analyze", json={})
    assert first.status_code == 202
    again = client.post(f"/api/v1/assets/{aid}/analyze", json={})
    assert again.status_code == 409 and again.json()["code"] == "ANALYSIS_RUNNING"
    assert again.json()["job_id"] == first.json()["id"]
    drain(client)
    assert client.post(f"/api/v1/assets/{aid}/analyze", json={}).status_code == 202  # allowed once finished
    drain(client)


def test_silent_video_uses_audio_mode(client: TestClient, tmp_path: Path, calls: list[int]) -> None:
    out = tmp_path / "noaudio.mp4"
    media_corpus._ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=40", "-c:v", "libx264", "-pix_fmt",
                     "yuv420p", str(out))  # fmt: skip
    aid = import_file(client, out)["id"]
    job = analyze(client, aid)
    assert calls == []  # no audio track -> no transcription attempted
    assert job["result"]["mode"] == "audio" and job["result"]["words"] == 0
    assert any("no audio" in n for n in job["result"]["notes"])
