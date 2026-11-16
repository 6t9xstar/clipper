"""End-to-end import: real API, real chunked uploads, real worker, real FFmpeg on a generated corpus."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import clipper.media.importer  # noqa: F401  (registers handler)
from clipper.api.app import create_app
from clipper.core.config import Settings
from clipper.core.services import Services
from clipper.jobs.worker import Worker
from clipper.maintenance import sweep

from . import media_corpus
from .util import authed

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="session")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return media_corpus.build(tmp_path_factory.mktemp("corpus"))


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with authed(create_app(settings)) as c:
        yield c


def svc(c: TestClient) -> Services:
    s: Services = c.app.state.clipper  # type: ignore[attr-defined]
    return s


def drain(c: TestClient, timeout: float = 120) -> None:
    """Run the worker in-process until no job is left."""
    w = Worker(svc(c).queue, services=svc(c), worker_id="test")
    end = time.time() + timeout
    while time.time() < end:
        if not w.run_once():
            return
    raise AssertionError("jobs did not finish")


def project(c: TestClient) -> str:
    pid: str = c.post("/api/v1/projects", json={"name": "P"}).json()["id"]
    return pid


def create(c: TestClient, pid: str, name: str, size: int, **kw: Any) -> Any:
    body = {
        "filename": name,
        "size_bytes": size,
        "rights_type": "own_content",
        "rights_confirmed": True,
        **kw,
    }
    return c.post(f"/api/v1/projects/{pid}/assets", json=body)


def upload(
    c: TestClient, pid: str, path: Path, chunk: int = 256 * 1024, name: str | None = None
) -> dict[str, Any]:
    data = path.read_bytes()
    r = create(c, pid, name or path.name, len(data))
    assert r.status_code == 201, r.json()
    a: dict[str, Any] = r.json()
    off = 0
    while off < len(data):
        r = c.put(a["upload_url"], content=data[off : off + chunk], headers={"upload-offset": str(off)})
        assert r.status_code == 200, r.json()
        off = r.json()["received_bytes"]
    return dict(r.json())


def import_file(c: TestClient, path: Path, name: str | None = None) -> dict[str, Any]:
    pid = project(c)
    a = upload(c, pid, path, name=name)
    assert a["status"] == "processing"
    drain(c)
    got: dict[str, Any] = c.get(f"/api/v1/assets/{a['id']}").json()
    return got


@pytest.mark.parametrize(
    "name", ["good_h264.mp4", "good_vp9.webm", "good_hevc.mkv", "rotated.mp4", "vfr.mkv"]
)
def test_valid_files_become_ready(
    client: TestClient, corpus: dict[str, Path], name: str, settings: Settings
) -> None:
    if name not in corpus:
        pytest.skip(f"{name}: encoder/option not available in this FFmpeg build")
    a = import_file(client, corpus[name])
    assert a["status"] == "ready", a
    assert a["sha256"] and len(a["sha256"]) == 64
    mi = a["media_info"]
    # proxy plays, is seekable (Range), has the right orientation
    r = client.get(f"/api/v1/assets/{a['id']}/proxy", headers={"range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    proxy = (
        settings.data_dir
        / "storage"
        / "projects"
        / a["project_id"]
        / "assets"
        / a["id"]
        / "derived"
        / "proxy.mp4"
    )
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:stream_side_data=rotation", "-of", "json", str(proxy)],
                         capture_output=True, text=True, check=True).stdout  # fmt: skip
    s = json.loads(out)["streams"][0]
    assert (s["width"], s["height"]) == (mi["proxy"]["width"], mi["proxy"]["height"])
    assert (s["width"] > s["height"]) == (a["width"] > a["height"])  # rotation baked in correctly
    assert not s.get("side_data_list")
    thumbs = client.get(f"/api/v1/assets/{a['id']}/thumbnails")
    assert thumbs.status_code == 200 and thumbs.content[:2] == b"\xff\xd8"
    wf = client.get(f"/api/v1/assets/{a['id']}/waveform").json()
    assert wf["rate"] == 20 and abs(len(wf["peaks"]) - a["duration_ms"] / 50) <= 3
    mid = sorted(wf["peaks"])[len(wf["peaks"]) // 2]
    assert 28 <= mid <= 36  # lavfi sine amplitude is 1/8 -> 255/8 ~= 32
    if name == "vfr.mkv":
        assert "VARIABLE_FRAME_RATE" in a["warnings"]
    if name == "rotated.mp4":
        assert (a["width"], a["height"]) == (240, 320)


def test_no_audio_is_ready_with_warning(client: TestClient, corpus: dict[str, Path]) -> None:
    a = import_file(client, corpus["noaudio.mp4"])
    assert a["status"] == "ready" and a["has_audio"] is False and "NO_AUDIO" in a["warnings"]
    assert client.get(f"/api/v1/assets/{a['id']}/waveform").json() == {"rate": 0, "peaks": []}


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("audio_only.mp4", "MEDIA_NO_VIDEO"),
        ("image.mp4", "MEDIA_UNSUPPORTED_CONTAINER"),
        ("text.mp4", "MEDIA_UNREADABLE"),
        ("random.mp4", "MEDIA_UNREADABLE"),
        ("truncated_no_moov.mp4", "MEDIA_UNREADABLE"),
        ("truncated_tail.mp4", "MEDIA_TRUNCATED"),
        ("playlist.mp4", "MEDIA_UNREADABLE"),
        ("qtrle.mov", "MEDIA_UNSUPPORTED_CODEC"),
        ("too_short.mp4", "MEDIA_TOO_SHORT"),
        ("tiny.mp4", "MEDIA_RESOLUTION_UNSUPPORTED"),
    ],
)
def test_bad_files_fail_with_clear_codes(
    client: TestClient, corpus: dict[str, Path], name: str, code: str, settings: Settings
) -> None:
    a = import_file(client, corpus[name])
    assert a["status"] == "failed" and a["error_code"] == code, a
    assert a["error_message"] and a["error_hint"]
    job = client.get(f"/api/v1/jobs/{a['import_job_id']}").json()
    assert job["status"] == "failed" and job["attempts"] == 1  # input errors are not retried
    folder = settings.data_dir / "storage" / "projects" / a["project_id"] / "assets" / a["id"]
    assert not any(folder.rglob("*.part")) and not (folder / "source").exists()  # bytes freed
    assert client.get(f"/api/v1/assets/{a['id']}/proxy").status_code == 409


def test_too_long_uses_setting(tmp_path: Path, corpus: dict[str, Path]) -> None:
    s = Settings(data_dir=tmp_path / "d", log_json=False, max_duration_seconds=2)
    with authed(create_app(s)) as c:
        assert import_file(c, corpus["good_h264.mp4"])["error_code"] == "MEDIA_TOO_LONG"


def test_resumable_upload_and_offsets(client: TestClient, corpus: dict[str, Path]) -> None:
    data = corpus["good_h264.mp4"].read_bytes()
    pid = project(client)
    a = create(client, pid, "My Video.mp4", len(data)).json()
    url = a["upload_url"]
    assert (
        client.put(url, content=data[:1000], headers={"upload-offset": "0"}).json()["received_bytes"] == 1000
    )
    # client lost track and retries from 0 -> told where to resume
    r = client.put(url, content=data[:1000], headers={"upload-offset": "0"})
    assert r.status_code == 409 and r.json()["code"] == "OFFSET_MISMATCH" and r.json()["offset"] == 1000
    assert client.get(url).json()["offset"] == 1000
    # sending more than declared is rejected and nothing of that chunk is kept
    r = client.put(url, content=data[1000:] + b"extra", headers={"upload-offset": "1000"})
    assert r.status_code == 400 and r.json()["code"] == "UPLOAD_EXCEEDS_SIZE"
    assert client.get(url).json()["offset"] == 1000
    r = client.put(url, content=data[1000:], headers={"upload-offset": "1000"})
    assert r.json()["status"] == "processing"
    assert (
        client.put(url, content=b"x", headers={"upload-offset": str(len(data))}).json()["code"]
        == "UPLOAD_CLOSED"
    )
    drain(client)
    done = client.get(f"/api/v1/assets/{a['id']}").json()
    assert done["status"] == "ready" and done["display_name"] == "My Video.mp4"
    import hashlib

    assert done["sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"rights_confirmed": False}, 422),
        ({"rights_type": "stolen"}, 422),
        ({"rights_type": "other"}, 422),  # note required
        ({"rights_type": "other", "rights_note": "Creator emailed me permission on 2026-11-01"}, 201),
        ({"filename": "evil.exe"}, 422),
        ({"filename": "../../../etc/clip.mp4"}, 201),  # path stripped, display only
        ({"size_bytes": 0}, 422),
        ({"size_bytes": 10**15}, 413),
    ],
)
def test_create_validation(client: TestClient, body: dict[str, Any], status: int) -> None:
    pid = project(client)
    base = {"filename": "a.mp4", "size_bytes": 1000, "rights_type": "own_content", "rights_confirmed": True}
    r = client.post(f"/api/v1/projects/{pid}/assets", json={**base, **body})
    assert r.status_code == status, r.json()
    if status == 201 and "filename" in body:
        assert r.json()["display_name"] == "clip.mp4"


def test_disk_full_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc(client).storage, "free_bytes", lambda: 100 * 1024**2)
    r = create(client, project(client), "a.mp4", 50 * 1024**2)
    assert r.status_code == 507 and r.json()["code"] == "DISK_FULL"


def test_delete_asset_and_project_remove_files(
    client: TestClient, corpus: dict[str, Path], settings: Settings
) -> None:
    a = import_file(client, corpus["good_h264.mp4"])
    folder = settings.data_dir / "storage" / "projects" / a["project_id"] / "assets" / a["id"]
    assert (folder / "derived" / "proxy.mp4").exists()
    assert client.delete(f"/api/v1/assets/{a['id']}").status_code == 204
    assert not folder.exists()
    assert client.get(f"/api/v1/assets/{a['id']}").status_code == 404
    b = import_file(client, corpus["noaudio.mp4"])
    assert client.delete(f"/api/v1/projects/{b['project_id']}").status_code == 204
    assert client.get(f"/api/v1/assets/{b['id']}").status_code == 404  # DB cascade
    assert not (settings.data_dir / "storage" / "projects" / b["project_id"]).exists()


def test_cancelled_import_is_reported(client: TestClient, corpus: dict[str, Path]) -> None:
    pid = project(client)
    a = upload(client, pid, corpus["good_h264.mp4"])
    client.post(f"/api/v1/jobs/{a['import_job_id']}/cancel")
    got = client.get(f"/api/v1/assets/{a['id']}").json()
    assert got["status"] == "failed" and got["error_code"] == "IMPORT_CANCELLED" and got["error_hint"]


def test_sweeper_expires_abandoned_uploads(client: TestClient, settings: Settings) -> None:
    pid = project(client)
    a = create(client, pid, "a.mp4", 5000).json()
    client.put(a["upload_url"], content=b"x" * 100, headers={"upload-offset": "0"})
    part = settings.data_dir / "storage" / "projects" / pid / "assets" / a["id"] / "upload.part"
    assert part.exists()
    assert sweep(svc(client))["expired_uploads"] == 0  # fresh
    res = sweep(svc(client), now=time.time() + settings.temp_retention_hours * 3600 + 60)
    assert res["expired_uploads"] == 1 and not part.exists()
    assert client.get(f"/api/v1/assets/{a['id']}").json()["error_code"] == "UPLOAD_ABANDONED"
