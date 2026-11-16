from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from clipper.api.app import create_app
from clipper.core.config import Settings
from clipper.jobs import builtin  # noqa: F401
from clipper.jobs.worker import Worker

H = {"host": "127.0.0.1"}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as c:
        yield c


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/readyz")
    body = r.json()
    assert body["checks"]["database"] == "ok"
    assert r.status_code == (200 if shutil.which("ffmpeg") else 503)


def test_project_crud(client: TestClient, settings: Settings) -> None:
    r = client.post("/api/v1/projects", json={"name": "  My   Podcast  "})
    assert r.status_code == 201
    p = r.json()
    assert p["name"] == "My Podcast"
    assert client.get(f"/api/v1/projects/{p['id']}").json()["id"] == p["id"]
    assert client.patch(f"/api/v1/projects/{p['id']}", json={"name": "Renamed"}).json()["name"] == "Renamed"
    for i in range(3):
        client.post("/api/v1/projects", json={"name": f"p{i}"})
    page = client.get("/api/v1/projects", params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    rest = client.get("/api/v1/projects", params={"limit": 10, "cursor": page["next_cursor"]}).json()
    assert len(rest["items"]) == 2
    # deleting removes DB row AND files
    files = settings.data_dir / "storage" / "projects" / p["id"]
    files.mkdir(parents=True)
    (files / "x.bin").write_bytes(b"1")
    assert client.delete(f"/api/v1/projects/{p['id']}").status_code == 204
    assert not files.exists()
    r = client.get(f"/api/v1/projects/{p['id']}")
    assert r.status_code == 404 and r.json()["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.parametrize("name", ["", "   ", "a\x00b", "x" * 201])
def test_project_validation(client: TestClient, name: str) -> None:
    r = client.post("/api/v1/projects", json={"name": name})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_bad_ids_are_404_not_500(client: TestClient) -> None:
    for path in ("/api/v1/projects/..%2F..%2Fetc", "/api/v1/jobs/zzz", "/api/v1/projects/" + "a" * 500):
        assert client.get(path).status_code == 404


def test_dns_rebinding_host_rejected(client: TestClient) -> None:
    r = client.get("/api/v1/projects", headers={"host": "evil.example"})
    assert r.status_code == 400 and r.json()["code"] == "HOST_NOT_ALLOWED"


def test_cross_origin_write_blocked(client: TestClient) -> None:
    r = client.post("/api/v1/projects", json={"name": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.post("/api/v1/projects", json={"name": "x"}, headers={"origin": "http://127.0.0.1:8765"})
    assert ok.status_code == 201


def test_only_whitelisted_job_types(client: TestClient) -> None:
    assert client.post("/api/v1/jobs", json={"type": "render.export"}).status_code == 422


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_selftest_job_end_to_end(client: TestClient) -> None:
    st = client.app.state.clipper  # type: ignore[attr-defined]
    w = Worker(st.queue, poll_seconds=0.05)
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    try:
        job = client.post(
            "/api/v1/jobs", json={"type": "system.selftest", "payload": {"seconds": 0.2}}
        ).json()
        assert job["status"] == "queued"
        end = time.time() + 30
        while time.time() < end:
            j = client.get(f"/api/v1/jobs/{job['id']}").json()
            if j["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.1)
        assert j["status"] == "succeeded", j
        assert j["result"]["ffmpeg"] == "ok"
        assert client.post(f"/api/v1/jobs/{job['id']}/retry").status_code == 409
        listed = client.get("/api/v1/jobs", params={"status": "succeeded"}).json()["items"]
        assert any(x["id"] == job["id"] for x in listed)
    finally:
        w.stop()
        t.join(5)
