"""M4 backend: access token, web UI serving, caption grouping, framing math, clip edits, transcript edits."""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from clipper.api.app import create_app
from clipper.core.config import Settings
from clipper.edit import captions as cap
from clipper.edit.framing import crop_rect
from clipper.edit.schema import ClipEdit, normalised

from .test_analyze import analyze, calls, client, clips, video  # noqa: F401  (fixtures)
from .test_import import import_file

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ token and web UI


@pytest.fixture
def raw(settings: Settings) -> Iterator[TestClient]:
    """Client WITHOUT the token, like a foreign program or web page."""
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as c:
        yield c


def test_api_requires_token(raw: TestClient, settings: Settings) -> None:
    assert raw.get("/healthz").status_code == 200  # health stays open for monitoring
    r = raw.get("/api/v1/projects")
    assert r.status_code == 401 and r.json()["code"] == "TOKEN_REQUIRED"
    assert raw.get("/api/v1/projects", headers={"x-clipper-token": "x" * 43}).status_code == 401
    token = (settings.data_dir / "api_token").read_text().strip()
    assert len(token) >= 32
    assert raw.get("/api/v1/projects", headers={"x-clipper-token": token}).status_code == 200


def test_page_grants_cookie_with_strict_flags_and_csp(raw: TestClient) -> None:
    r = raw.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    sc = r.headers["set-cookie"].lower()
    assert "clipper_token=" in sc and "httponly" in sc and "samesite=strict" in sc
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe" not in csp
    # the cookie now authorises API calls from the page (same origin)
    assert raw.get("/api/v1/projects").status_code == 200


def test_spa_routes_and_api_404(raw: TestClient) -> None:
    raw.get("/")
    assert "text/html" in raw.get("/projects/abc/assets/def").headers["content-type"]
    r = raw.get("/api/v1/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    assert raw.get("/..%2f..%2fpyproject.toml").headers["content-type"].startswith("text/html")


def test_built_ui_routes_do_not_collide_with_static_files(tmp_path: Path) -> None:
    """Regression: build files used to live under /assets, which shadowed the SPA route /assets/<id>."""
    from fastapi import FastAPI

    from clipper.api import web

    dist = tmp_path / "dist"
    (dist / "static").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>")
    (dist / "static" / "app.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    app = FastAPI()
    web.install(app, "t" * 43, dist)
    c = TestClient(app)
    for path in ("/assets/01abc", "/clips/01abc", "/projects/x", "/static"):
        r = c.get(path)
        assert r.status_code == 200 and "id=root" in r.text, path
    assert c.get("/static/app.js").text == "console.log(1)"
    assert c.get("/static/missing.js").status_code == 404
    assert c.get("/favicon.svg").text == "<svg/>"
    assert "id=root" in c.get("/index.html").text  # served as a page (cookie + CSP), not as a raw file


def test_token_is_stable_across_restarts(settings: Settings) -> None:
    a = create_app(settings).state.token
    b = create_app(settings).state.token
    assert a == b


# ------------------------------------------------------------------ framing


def test_framing_matches_shared_vectors() -> None:
    cases = json.loads((ROOT / "testdata" / "framing_cases.json").read_text())["cases"]
    assert len(cases) >= 30
    for c in cases:
        r = crop_rect(*c["src"], c["x"], c["y"], c["zoom"])
        assert [r.x, r.y, r.w, r.h] == pytest.approx(c["rect"], abs=1e-3), c


def test_framing_hand_checked() -> None:
    r = crop_rect(1920, 1080)  # centred 9:16 window of a 16:9 frame
    assert (r.w, r.h) == (607.5, 1080) and r.x == pytest.approx((1920 - 607.5) / 2)
    assert crop_rect(1920, 1080, x=0).x == 0 and crop_rect(1920, 1080, x=1).x == pytest.approx(1920 - 607.5)
    v = crop_rect(1080, 1920)  # already vertical: whole frame
    assert (v.x, v.y, v.w, v.h) == (0, 0, 1080, 1920)
    z = crop_rect(1920, 1080, zoom=2)
    assert z.h == 540 and z.y == 270
    with pytest.raises(ValueError):
        crop_rect(0, 1080)


# ------------------------------------------------------------------ captions


def W(text: str, start: float = 0.0, step: float = 0.4) -> list[dict[str, Any]]:
    return [{"w": t, "s": start + k * step, "e": start + k * step + 0.3} for k, t in enumerate(text.split())]


def test_groups_respect_words_lines_sentences_and_gaps() -> None:
    p = cap.PRESETS["bold"]  # 3 words, 16 chars/line, 2 lines
    words = W("one two three four five. six seven", start=10.0)
    words += W("after pause", start=words[-1]["e"] + 1.0)
    g = cap.group_words(words, 10.0, 30.0, p)
    texts = [" ".join(w["w"] for line in x["lines"] for w in line) for x in g]
    assert texts == ["one two three", "four five.", "six seven", "after pause"]
    assert g[0]["s"] == 0.0  # relative to clip start
    assert all(a["e"] <= b["s"] + 1e-9 for a, b in itertools.pairwise(g))  # never overlap
    assert all(x["e"] > x["s"] for x in g)


def test_lines_are_balanced_and_bounded() -> None:
    p = cap.PRESETS["clean"]  # 7 words, 22 chars/line
    g = cap.group_words(W("this sentence has quite a lot of words"), 0, 10, p)
    for grp in g:
        assert len(grp["lines"]) <= p.max_lines
        for line in grp["lines"]:
            assert len(" ".join(w["w"] for w in line)) <= p.max_chars_per_line


def test_words_outside_clip_and_hidden_words_skipped() -> None:
    words = W("before inside also after", start=0, step=2.0)
    words[2]["w"] = ""  # hidden in the transcript editor
    g = cap.group_words(words, 1.9, 5.0, cap.PRESETS["box"])
    assert [w["w"] for x in g for line in x["lines"] for w in line] == ["inside"]


def test_edit_normalisation() -> None:
    full = normalised({})
    assert full["captions"]["preset"] == cap.DEFAULT_PRESET and full["framing"]["mode"] == "crop"
    assert normalised({"captions": {"preset": "removed-preset"}}) == full  # stale data never breaks reads


# ------------------------------------------------------------------ API: clip edits, captions, transcript


@pytest.fixture
def analysed(client: TestClient, video: Path, calls: list[int]) -> tuple[str, dict[str, Any]]:  # noqa: F811
    aid = import_file(client, video)["id"]
    analyze(client, aid)
    return aid, clips(client, aid)[0]


def test_clip_edit_roundtrip(client: TestClient, analysed: tuple[str, dict[str, Any]]) -> None:  # noqa: F811
    _, clip = analysed
    assert clip["edit"]["captions"]["preset"] == "bold" and clip["edit"]["framing"]["zoom"] == 1.0
    url = f"/api/v1/clips/{clip['id']}"
    edit = {"captions": {"enabled": True, "preset": "gaming", "position": 0.5},
            "framing": {"mode": "crop", "x": 0.3, "y": 0.5, "zoom": 1.5}}  # fmt: skip
    r = client.patch(url, json={"edit": edit}, headers={"if-match": '"1"'})
    assert r.status_code == 200, r.json()
    full = {**ClipEdit().model_dump(), **edit}  # missing parts come back with their defaults
    assert r.json()["edit"] == full and r.json()["version"] == 2
    assert client.get(url).json()["edit"] == full
    bad = client.patch(url, json={"edit": {"captions": {"preset": "nope"}}}, headers={"if-match": '"2"'})
    assert bad.status_code == 422
    extra = client.patch(url, json={"edit": {"framing": {"x": 0.5, "evil": 1}}}, headers={"if-match": '"2"'})
    assert extra.status_code == 422
    zoom = client.patch(url, json={"edit": {"framing": {"zoom": 9}}}, headers={"if-match": '"2"'})
    assert zoom.status_code == 422


def test_captions_endpoint(client: TestClient, analysed: tuple[str, dict[str, Any]]) -> None:  # noqa: F811
    _, clip = analysed
    r = client.get(f"/api/v1/clips/{clip['id']}/captions").json()
    assert r["preset"]["id"] == "bold" and r["groups"]
    dur = (clip["end_ms"] - clip["start_ms"]) / 1000
    assert all(0 <= g["s"] < g["e"] <= dur + 1e-6 for g in r["groups"])
    assert r["groups"][0]["lines"][0][0]["w"] == "Why"
    other = client.get(f"/api/v1/clips/{clip['id']}/captions", params={"preset": "clean"}).json()
    assert other["preset"]["id"] == "clean" and len(other["groups"]) < len(r["groups"])
    assert client.get(f"/api/v1/clips/{clip['id']}/captions", params={"preset": "x"}).status_code == 422
    # unsaved trim preview: later start -> the first caption no longer starts with the first word
    later = {"start_ms": clip["start_ms"] + 3000, "end_ms": clip["end_ms"]}
    moved = client.get(f"/api/v1/clips/{clip['id']}/captions", params=later).json()
    assert moved["groups"][0]["lines"][0][0]["w"] != "Why"
    oob = client.get(f"/api/v1/clips/{clip['id']}/captions", params={"start_ms": 0, "end_ms": 10**9})
    assert oob.status_code == 422
    presets = client.get("/api/v1/caption-presets").json()
    assert {p["id"] for p in presets["items"]} >= {"bold", "clean", "box", "gaming"}


def test_transcript_edit(client: TestClient, analysed: tuple[str, dict[str, Any]]) -> None:  # noqa: F811
    aid, clip = analysed
    url = f"/api/v1/assets/{aid}/transcript"
    t = client.get(url)
    assert t.headers["etag"] == '"1"' and t.json()["edited"] is False
    first = t.json()["words"][0]
    assert client.patch(url, json={"words": [{"i": 0, "w": "How"}]}).status_code == 428
    r = client.patch(
        url, json={"words": [{"i": 0, "w": "How"}, {"i": 1, "w": ""}]}, headers={"if-match": '"1"'}
    )
    assert r.status_code == 200 and r.headers["etag"] == '"2"'
    body = r.json()
    assert body["edited"] is True and body["words"][0]["w"] == "How" and body["words"][1]["w"] == ""
    assert body["words"][0]["s"] == first["s"]  # timings untouched
    stale = client.patch(url, json={"words": [{"i": 0, "w": "x"}]}, headers={"if-match": '"1"'})
    assert stale.status_code == 412 and stale.json()["code"] == "VERSION_CONFLICT"
    oob = client.patch(url, json={"words": [{"i": 10**6, "w": "x"}]}, headers={"if-match": '"2"'})
    assert oob.status_code == 422 and oob.json()["code"] == "WORD_INDEX"
    ctrl = client.patch(url, json={"words": [{"i": 0, "w": "a\u0000b"}]}, headers={"if-match": '"2"'})
    assert ctrl.status_code == 422

    caps = client.get(f"/api/v1/clips/{clip['id']}/captions").json()["groups"]
    shown = [w["w"] for g in caps for line in g["lines"] for w in line]
    assert shown[0] == "How" and first["w"] not in shown[:1] and "do" not in shown[:1]

    # re-transcribing would destroy the corrections: refused unless explicitly confirmed
    r = client.post(f"/api/v1/assets/{aid}/analyze", json={"retranscribe": True})
    assert r.status_code == 409 and r.json()["code"] == "TRANSCRIPT_EDITED"
    r = client.post(f"/api/v1/assets/{aid}/analyze", json={"retranscribe": True, "discard_edits": True})
    assert r.status_code == 202
