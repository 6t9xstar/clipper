"""M5 render & export.

Unit tests for the pure builders, then "golden" checks on real renders. Instead of stored reference images
(brittle across FFmpeg/libass versions) the output is measured: frame size and duration, colours sampled where
the crop must show a known part of the source, highlight-coloured caption pixels while a word is spoken,
caption glyph size and baseline against the font maths the browser preview uses, and loudness with ebur128.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

import clipper.render.export  # noqa: F401  (registers handler)
from clipper.api.app import create_app
from clipper.core.config import Settings
from clipper.edit import captions as cap
from clipper.render import ass, fontinfo, graph, subs

from . import media_corpus
from .test_analyze import analyze, calls, fake_words  # noqa: F401  (fixture)
from .test_import import drain, import_file, pytestmark, svc  # noqa: F401  (pytestmark: needs ffmpeg)
from .util import authed

# ------------------------------------------------------------------ pure builders


def test_colours_and_escaping() -> None:
    assert ass.colour("#FFE14D") == "&H004DE1FF"
    assert ass.colour("#000000B3") == "&H4C000000"  # ASS alpha is inverted
    with pytest.raises(ValueError):
        ass.colour("red")
    e = ass.escape("a{\\b1}b\\Nc\n")
    assert "{" not in e and "}" not in e and "\\" not in e and "\n" not in e


def words(text: str, start: float = 0.0, step: float = 0.5) -> list[dict[str, Any]]:
    return [
        {"w": w, "s": start + i * step, "e": start + i * step + 0.4, "p": 1}
        for i, w in enumerate(text.split())
    ]


def test_ass_events_highlight_each_word_on_its_own_line_positions() -> None:
    p = cap.PRESETS["bold"]
    g = cap.group_words(words("one two three four"), 0, 10, p)
    doc = ass.build(g, p, None, 10)
    events = [ln for ln in doc.splitlines() if ln.startswith("Dialogue:")]
    hl = ass._inline(p.highlight or "")
    # every event is a single line; exactly one word is highlighted while it is spoken
    assert all("\\N" not in ev for ev in events)
    spoken = [ev for ev in events if hl in ev]
    assert len(spoken) == 4 and all(ev.count(hl) == 1 for ev in spoken)
    assert "Fontname" in doc and "Montserrat Black" in doc and "PlayResY: 1920" in doc
    ys = sorted({float(m) for m in re.findall(r"\\pos\(540,([\d.]+)\)", doc)})
    centres = cap.line_centres(len(g[0]["lines"]), p, None)
    assert len(ys) == len(centres) and all(
        abs(y - c - ass.baseline_offset(p)) < 0.06 for y, c in zip(ys, centres, strict=True)
    )
    # custom position moves the block
    doc2 = ass.build(g, p, 0.2, 10)
    assert float(re.findall(r"\\pos\(540,([\d.]+)\)", doc2)[0]) < 0.3 * 1920


def test_long_lines_are_scaled_to_fit() -> None:
    p = cap.PRESETS["clean"]
    assert cap.fit("short", p) == 1.0
    wide = "W" * p.max_chars_per_line
    f = cap.fit(wide, p)
    assert f < 1.0
    assert fontinfo.metrics(p.font_file).width(wide, p.size * f) + 2 * p.outline <= 960 + 1
    g = cap.group_words([{"w": wide, "s": 0, "e": 1, "p": 1}], 0, 2, p)
    assert g[0]["fit"] == [f]
    assert f"\\fscx{f * 100:.2f}" in ass.build(g, p, None, 2)


def test_srt_and_vtt() -> None:
    g = cap.group_words(words("Hello there. <b>bold</b> & more", 1.0), 0, 10, cap.PRESETS["clean"])
    s = subs.srt(g)
    assert s.startswith("1\n00:00:01,000 --> ") and "Hello there." in s
    v = subs.vtt(g)
    assert v.startswith("WEBVTT\n\n00:00:01.000 --> ") and "&lt;b&gt;bold&lt;/b&gt; &amp; more" in v
    assert "<b>" not in v


def test_filter_graphs() -> None:
    src = graph.Source(Path("x.mp4"), 1920, 1080, 59.94, False, True)
    edit = {
        "captions": {"enabled": True, "preset": "bold", "position": None},
        "framing": {"mode": "crop", "x": 0.0, "y": 0.5, "zoom": 1.0},
    }
    vf = graph.video_filter(src, edit, graph.QUALITIES["standard"], True)
    assert "crop=608:1080:0:0" in vf and "scale=1080:1920" in vf and "ass=captions.ass:fontsdir=fonts" in vf
    assert "fps=59.94" in vf
    edit["framing"]["mode"] = "fit"
    vf = graph.video_filter(src, edit, graph.QUALITIES["draft"], False)
    assert "gblur" in vf and "overlay" in vf and "ass=" not in vf and "720:1280" in vf
    assert graph.out_fps(120) == 60 and graph.out_fps(0) == 30
    assert graph.audio_plan(None).filter == "aresample=48000"
    m = {"input_i": -70.0, "input_tp": -60.0, "input_lra": 0.0, "input_thresh": -80.0, "target_offset": 0.0}
    assert graph.audio_plan(m).mode == "untouched"  # never amplify near-silence
    lin = graph.audio_plan({**m, "input_i": -25.0, "input_tp": -15.0})  # 11 dB gain, peaks end at -4 dBTP
    assert (lin.mode, lin.gain_db) == ("linear", 11.0) and lin.filter.startswith("volume=11.00dB")
    lim = graph.audio_plan({**m, "input_i": -20.0, "input_tp": -0.4})  # peaks already near full scale
    assert lim.mode == "limited" and "alimiter" in lim.filter and not lim.capped
    capped = graph.audio_plan({**m, "input_i": -20.0, "input_tp": -0.4}, gain_db=15.0)
    assert capped.capped and capped.gain_db == round(-1.5 + 0.4 + graph.MAX_LIMITING_DB, 2)
    argv = graph.render_cmd(
        src,
        1.0,
        5.0,
        edit,
        graph.QUALITIES["standard"],
        captions=False,
        audio=graph.audio_plan(None),
        out=Path("o.mp4"),
    )
    assert argv[argv.index("-map_metadata") + 1] == "-1"
    assert "-protocol_whitelist" in argv


def test_parsers() -> None:
    err = (
        'blah\n[Parsed_loudnorm_0 @ 0x1]\n{\n\t"input_i" : "-23.51",\n\t"input_tp" : "-8.02",\n'
        '\t"input_lra" : "1.20",\n\t"input_thresh" : "-33.90",\n\t"output_i" : "-14.0",\n'
        '\t"target_offset" : "0.12"\n}\n'
    )
    m = graph.parse_loudnorm(err)
    assert m is not None and m["input_i"] == -23.51 and m["target_offset"] == 0.12
    assert graph.parse_loudnorm('{"input_i" : "-inf"}') is None
    assert graph.parse_ebur128("  Integrated loudness:\n    I:         -14.1 LUFS\n") == -14.1
    assert graph.parse_ebur128("I:  -inf LUFS") is None
    assert graph.parse_true_peak("  True peak:  Peak:        -1.6 dBFS") == -1.6


# ------------------------------------------------------------------ real renders: fonts vs preview maths


def _gray(cmd: list[str], w: int = 1080, h: int = 1920) -> np.ndarray:
    r = subprocess.run(
        [*cmd, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True
    )
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w)


@pytest.mark.parametrize("preset_id", sorted(cap.PRESETS))
def test_libass_glyphs_match_browser_font_maths(preset_id: str, tmp_path: Path) -> None:
    """Cap height and baseline of rendered text = what the preview computes (P5: error <= 2 % of frame)."""
    p = cap.PRESETS[preset_id]
    p = cap.Preset(**{**p.__dict__, "outline": 0, "shadow": 0, "box": None, "highlight": None})
    g = [{"s": 0.0, "e": 2.0, "lines": [[{"w": "HHHH", "s": 0.0, "e": 2.0}]], "fit": [1.0]}]
    (tmp_path / "captions.ass").write_text(ass.build(g, p, 0.5, 2.0), encoding="utf-8")
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / p.font_file).write_bytes((fontinfo.FONTS_DIR / p.font_file).read_bytes())
    r = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=1080x1920:d=1",
            "-vf",
            "ass=captions.ass:fontsdir=fonts",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
        cwd=tmp_path,
    )
    a = np.frombuffer(r.stdout, np.uint8).reshape(1920, 1080)
    ys = np.where((a > 128).any(axis=1))[0]
    f = p.font
    cap_h = p.size * f.cap_height / f.units_per_em
    assert abs((ys.max() - ys.min() + 1) - cap_h) <= max(2.0, 0.03 * cap_h), "font size differs from preview"
    # browser: line box centred at 960, content area (typo metrics) centred in it -> baseline position
    baseline = 960 + (f.typo_ascent - f.typo_descent) / 2 * p.size / f.units_per_em
    assert abs((ys.max() + 1) - baseline) <= 3, "baseline differs from preview"  # 3 px = 0.16 % of 1920


# ------------------------------------------------------------------ export end to end


@pytest.fixture(scope="module")
def halves(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """12 s 1280x720: left half red, right half blue; a quiet tone (well below -14 LUFS)."""
    out = tmp_path_factory.mktemp("render") / "halves.mp4"
    media_corpus._ff(
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=1280x720:r=30:d=12",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=640x720:r=30:d=12",
        "-f",
        "lavfi",
        "-i",
        "sine=f=440:d=12,volume=0.05",
        "-filter_complex",
        "[0][1]overlay=640:0[v]",
        "-map",
        "[v]",
        "-map",
        "2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(out),
    )
    return out


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    st = settings.model_copy(update={"asr_model": "tiny", "asr_device": "cpu"})
    with authed(create_app(st)) as c:
        yield c


@pytest.fixture
def ready(client: TestClient, halves: Path, calls: list[int]) -> dict[str, Any]:  # noqa: F811
    a = import_file(client, halves)
    assert a["status"] == "ready", a
    analyze(client, a["id"])  # test-double transcript (fake_words), real everything else
    return a


def manual_clip(c: TestClient, aid: str, start_ms: int, end_ms: int, **edit: Any) -> dict[str, Any]:
    r = c.post(
        f"/api/v1/assets/{aid}/clips", json={"start_ms": start_ms, "end_ms": end_ms, "title": "My Clip!"}
    )
    assert r.status_code == 201, r.json()
    clip: dict[str, Any] = r.json()
    if edit:
        full = clip["edit"]
        for k, v in edit.items():
            full[k] = {**full[k], **v}
        r = c.patch(
            f"/api/v1/clips/{clip['id']}", json={"edit": full}, headers={"if-match": f'"{clip["version"]}"'}
        )
        assert r.status_code == 200, r.json()
        clip = r.json()
    return clip


def export(c: TestClient, clip_id: str, **body: Any) -> dict[str, Any]:
    r = c.post(f"/api/v1/clips/{clip_id}/exports", json=body)
    assert r.status_code == 202, r.json()
    drain(c)
    e: dict[str, Any] = c.get(f"/api/v1/exports/{r.json()['id']}").json()
    return e


def frame(path: Path, t: float, w: int = 1080, h: int = 1920) -> np.ndarray:
    r = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{t}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 3).astype(int)


def probe(path: Path) -> dict[str, Any]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        check=True,
    )
    out: dict[str, Any] = json.loads(r.stdout)
    return out


def lufs(path: Path) -> float:
    r = subprocess.run(graph.ebur128_cmd(path), capture_output=True, text=True, encoding="utf-8")
    v = graph.parse_ebur128(r.stderr)
    assert v is not None
    return v


def download(c: TestClient, e: dict[str, Any], kind: str, tmp: Path) -> Path:
    r = c.get(f"/api/v1/exports/{e['id']}/files/{kind}?download=1")
    assert r.status_code == 200, r.text
    assert (
        "attachment" in r.headers["content-disposition"]
        and f"my-clip-{e['id'][-6:]}.{kind}" in r.headers["content-disposition"]
    )
    p = tmp / f"out.{kind}"
    p.write_bytes(r.content)
    return p


def is_red(px: np.ndarray) -> bool:
    return bool(px[0] > 180 and px[1] < 70 and px[2] < 70)


def is_blue(px: np.ndarray) -> bool:
    return bool(px[2] > 180 and px[0] < 70 and px[1] < 70)


def test_export_crop_captions_loudness_and_files(
    client: TestClient, ready: dict[str, Any], tmp_path: Path
) -> None:
    aid = ready["id"]
    clip = manual_clip(
        client,
        aid,
        2000,
        8000,
        framing={"mode": "crop", "x": 0.0, "y": 0.5, "zoom": 1.0},
        captions={"preset": "bold"},
    )
    e = export(client, clip["id"])
    assert e["status"] == "done", e
    assert e["width"] == 1080 and e["height"] == 1920 and e["duration_ms"] == 6000
    assert set(e["files"]) == {"mp4", "srt", "vtt", "json"}
    assert all(c["ok"] for c in e["checks"].values()), e["checks"]

    mp4 = download(client, e, "mp4", tmp_path)
    info = probe(mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["codec_name"], v["width"], v["height"], v["pix_fmt"]) == ("h264", 1080, 1920, "yuv420p")
    assert abs(float(v["duration"]) - 6.0) <= 1 / 30 + 0.001
    assert (a["codec_name"], a["sample_rate"], a["channels"]) == ("aac", "48000", 2)
    assert "location" not in json.dumps(info["format"].get("tags", {})).lower()

    # independent loudness measurement: the quiet tone was normalised to -14 LUFS
    assert abs(lufs(mp4) - graph.TARGET_LUFS) <= 1.0

    # crop x=0 shows the left (red) part of the source; no blue anywhere above the captions
    f = frame(mp4, 0.5)
    top = f[100:900]
    assert is_red(top[400, 540]) and not any(is_blue(px) for px in top[::50, ::50].reshape(-1, 3))

    # a word is being spoken -> highlight-coloured (yellow) pixels in the caption band
    words_in = [w for w in fake_words() if w["s"] >= 2.0 and w["e"] <= 8.0]
    t = (words_in[0]["s"] + words_in[0]["e"]) / 2 - 2.0
    f = frame(mp4, t)
    band = f[int(0.70 * 1920) - 250 : int(0.70 * 1920) + 250]
    yellow = (band[..., 0] > 200) & (band[..., 1] > 190) & (band[..., 2] < 140)
    white = (band > 230).all(axis=-1)
    assert yellow.sum() > 500 and white.sum() > 500

    srt = download(client, e, "srt", tmp_path).read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,") and words_in[0]["w"] in srt
    manifest = json.loads(download(client, e, "json", tmp_path).read_text(encoding="utf-8"))
    assert manifest["rights"]["type"] == "own_content" and manifest["source"]["sha256"] == ready["sha256"]
    assert manifest["clip"]["start_ms"] == 2000 and manifest["captions"]["font"] == "Montserrat Black"

    # inline (no download) for the in-app player, with HTTP Range
    r = client.get(f"/api/v1/exports/{e['id']}/files/mp4", headers={"range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10

    hist = client.get(f"/api/v1/clips/{clip['id']}/exports").json()["items"]
    assert [x["id"] for x in hist] == [e["id"]]
    assert client.get(f"/api/v1/projects/{ready['project_id']}/exports").json()["items"][0]["id"] == e["id"]


def test_export_fit_right_crop_no_captions_draft(client: TestClient, ready: dict[str, Any]) -> None:
    aid = ready["id"]
    clip = manual_clip(
        client,
        aid,
        0,
        3000,
        framing={"mode": "crop", "x": 1.0, "y": 0.5, "zoom": 1.0},
        captions={"enabled": False},
    )
    e = export(client, clip["id"], quality="draft")
    assert e["status"] == "done" and (e["width"], e["height"]) == (720, 1280), e
    assert "srt" in e["files"]  # sidecars are still useful when nothing is burned in
    mp4 = svc(client).storage.path(f"projects/{ready['project_id']}/assets/{aid}/exports/{e['id']}/clip.mp4")
    f = frame(mp4, 0.5, 720, 1280)
    assert is_blue(f[640, 360]) and not (f[800:1100] > 230).all(axis=-1).any()  # right side, no caption text

    clip2 = manual_clip(client, aid, 0, 3000, framing={"mode": "fit"}, captions={"enabled": False})
    e2 = export(client, clip2["id"])
    assert e2["status"] == "done", e2
    mp4 = svc(client).storage.path(f"projects/{ready['project_id']}/assets/{aid}/exports/{e2['id']}/clip.mp4")
    f = frame(mp4, 0.5)
    mid = 960  # the whole 16:9 frame sits in the middle: red left, blue right
    assert is_red(f[mid, 100]) and is_blue(f[mid, 980])
    # above it: the blurred, darkened copy (no full-brightness colour)
    assert f[150, 540].max() < 0.75 * 255


def test_export_api_rules(client: TestClient, ready: dict[str, Any]) -> None:
    aid = ready["id"]
    clip = manual_clip(client, aid, 1000, 4000)
    cid = clip["id"]
    assert client.get("/api/v1/export-qualities").json()["default"] == "standard"
    assert client.post(f"/api/v1/clips/{cid}/exports", json={"quality": "8k"}).status_code == 422
    r = client.post(f"/api/v1/clips/{cid}/exports", json={}, headers={"if-match": '"99"'})
    assert r.status_code == 412 and r.json()["code"] == "VERSION_CONFLICT"

    a = client.post(f"/api/v1/clips/{cid}/exports", json={}, headers={"if-match": f'"{clip["version"]}"'})
    b = client.post(f"/api/v1/clips/{cid}/exports", json={})
    assert (
        a.status_code == 202 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    )  # no double render
    eid = a.json()["id"]
    assert client.get(f"/api/v1/exports/{eid}/files/mp4").status_code == 404  # not rendered yet

    # cancelled before a worker picked it up -> shown as cancelled
    client.post(f"/api/v1/jobs/{a.json()['job_id']}/cancel")
    assert client.get(f"/api/v1/exports/{eid}").json()["status"] == "cancelled"

    e = export(client, cid)
    folder = svc(client).storage.path(f"projects/{ready['project_id']}/assets/{aid}/exports/{e['id']}")
    assert (folder / "clip.mp4").exists() and not (folder / "work").exists()
    assert client.delete(f"/api/v1/exports/{e['id']}").status_code == 204
    assert not folder.exists() and client.get(f"/api/v1/exports/{e['id']}").status_code == 404

    # deleting a clip deletes its exported files
    e = export(client, cid)
    folder = svc(client).storage.path(f"projects/{ready['project_id']}/assets/{aid}/exports/{e['id']}")
    assert folder.exists()
    assert client.delete(f"/api/v1/clips/{cid}").status_code == 204
    assert not folder.exists()

    srt = client.get(f"/api/v1/clips/{manual_clip(client, aid, 1000, 4000)['id']}/captions.srt")
    assert srt.status_code == 200 and srt.text.startswith("1\n")


def test_export_without_audio(client: TestClient, tmp_path: Path) -> None:
    silent = tmp_path / "noaudio.mp4"
    media_corpus._ff(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=s=640x360:r=25:d=4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(silent),
    )
    a = import_file(client, silent)
    clip = manual_clip(client, a["id"], 0, 3000)
    e = export(client, clip["id"])
    assert e["status"] == "done", e
    assert e["checks"]["audio"] == {"ok": True, "value": None} and "loudness" not in e["checks"]
    assert set(e["files"]) == {"mp4", "json"}  # no transcript -> no sidecars


def test_export_peaky_audio_is_limited_not_clipped(client: TestClient, tmp_path: Path) -> None:
    """Quiet signal with full-scale clicks (like hard consonants): gain must go through the limiter, true peak
    stays under the ceiling, and if -14 LUFS is out of reach the shortfall is reported, not hidden."""
    src = tmp_path / "peaky.mp4"
    media_corpus._ff(
        "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=25:d=6",
        "-f", "lavfi", "-i", r"aevalsrc=0.03*sin(2*PI*300*t)+0.95*lt(mod(n\,4800)\,3):s=48000:d=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(src),
    )  # fmt: skip
    a = import_file(client, src)
    clip = manual_clip(client, a["id"], 0, 5000)
    e = export(client, clip["id"])
    assert e["status"] == "done", e
    loud = e["checks"]["loudness"]
    assert loud["mode"] == "limited", loud
    assert loud["true_peak"] is not None and loud["true_peak"] <= -1.0
    mp4 = svc(client).storage.path(f"projects/{a['project_id']}/assets/{a['id']}/exports/{e['id']}/clip.mp4")
    measured = lufs(mp4)
    assert abs(measured - loud["value"]) < 0.2
    if loud["ok"]:
        assert abs(measured - graph.TARGET_LUFS) <= 1.0
    else:  # honest shortfall
        assert loud["note"] and "peak limiting" in loud["note"] and measured < graph.TARGET_LUFS
