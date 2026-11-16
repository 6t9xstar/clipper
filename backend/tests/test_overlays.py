"""M6 text overlays: wrapping and layout, validation, the layout endpoint, rendered glyph positions against
the preview maths, and an export with overlays and no captions."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from clipper.edit import overlays as ov
from clipper.edit.captions import SIDE_MARGIN, Preset
from clipper.render import ass, fontinfo

from .test_analyze import calls  # noqa: F401  (fixture)
from .test_import import pytestmark  # noqa: F401  (needs ffmpeg)
from .test_render import client, export, frame, halves, ready  # noqa: F401  (fixtures)


def o(**kw: Any) -> dict[str, Any]:
    return {"id": "a1", "style": "title", "text": "Hello", "start": 0.0, "end": 2.0, "x": 0.5, "y": 0.5, **kw}


# ------------------------------------------------------------------ pure layout


def test_wrap_is_balanced_and_keeps_user_breaks() -> None:
    t = ov.STYLES["title"]  # 16 chars per line, uppercase
    assert ov.wrap("Look at the knee angle here", ov.STYLES["callout"]) == ["Look at the", "knee angle here"]
    assert ov.wrap("Chapter 2: the ten percent rule", t) == ["CHAPTER 2: THE", "TEN PERCENT RULE"]
    assert ov.wrap("Part one\nThe start", t) == ["PART ONE", "THE START"]
    assert ov.wrap("  \n\n ", t) == []
    long = "Supercalifragilisticexpialidocious"
    assert ov.wrap(f"{long} club", t) == [long.upper(), "CLUB"]  # over-long word alone, shrunk by fit


def test_layout_clamps_to_the_frame_and_to_the_clip() -> None:
    lo = ov.layout(o(style="callout", text="Look here", x=1.0, y=1.0), 10.0)
    assert lo is not None
    p = ov.STYLES["callout"]
    half = fontinfo.metrics(p.font_file).width("Look here", p.size) / 2 + p.box_padding
    assert abs(lo["x"] - (1080 - SIDE_MARGIN - half)) < 0.2  # pushed back inside the right margin
    assert lo["ys"][-1] + p.line_height * p.size / 2 <= 1920 - ov.TOP_BOTTOM_MARGIN + 0.2
    # end is cut to the clip length; an overlay entirely after the clip is dropped
    assert ov.layout(o(start=1.0, end=30.0), 5.0)["e"] == 5.0  # type: ignore[index]
    assert ov.layout(o(start=6.0, end=8.0), 5.0) is None
    assert ov.layout_all([o(), o(id="b2", start=9, end=12)], 5.0)[0]["id"] == "a1"
    # a very long word is scaled to fit between the margins
    lo = ov.layout(o(style="label", text="W" * 40), 5.0)
    assert lo is not None and lo["fit"][0] < 1
    lp = ov.STYLES["label"]
    assert fontinfo.metrics(lp.font_file).width("W" * 40, lp.size) * lo["fit"][0] + 2 * lp.outline <= 960 + 1


def test_ass_overlays_sit_above_captions_with_their_own_styles() -> None:
    lo = ov.layout_all([o(text="Title"), o(id="b2", style="callout", text="Callout", start=1, end=3)], 5.0)
    doc = ass.build([], ov.STYLES["title"], None, 5.0, lo, captions=False)
    styles = [ln.split(",")[0] for ln in doc.splitlines() if ln.startswith("Style:")]
    assert styles == ["Style: Caption", "Style: CaptionBox", "Style: Ovtitle", "Style: OvtitleBox",
                      "Style: Ovcallout", "Style: OvcalloutBox"]  # fmt: skip
    events = [ln for ln in doc.splitlines() if ln.startswith("Dialogue:")]
    assert {e.split(",")[0] for e in events} == {"Dialogue: 2", "Dialogue: 3"}  # boxes, then text
    assert any("0:00:01.00,0:00:03.00,Ovcallout," in e for e in events)
    assert ass.fonts_for(ov.STYLES["title"], lo) == {"Montserrat-Black.ttf", "Montserrat-ExtraBold.ttf"}


# ------------------------------------------------------------------ validation and the layout endpoint


def _patch(c: TestClient, clip: dict[str, Any], overlays: list[dict[str, Any]]) -> Any:
    return c.patch(
        f"/api/v1/clips/{clip['id']}",
        json={"edit": {**clip["edit"], "overlays": overlays}},
        headers={"if-match": f'"{clip["version"]}"'},
    )


def test_overlay_validation_and_layout_endpoint(
    client: TestClient,  # noqa: F811
    ready: dict[str, Any],  # noqa: F811
) -> None:
    r = client.post(f"/api/v1/assets/{ready['id']}/clips", json={"start_ms": 0, "end_ms": 6000})
    clip = r.json()
    assert clip["edit"]["overlays"] == []  # default for clips made before overlays existed

    bad = [
        [o(), o()],  # duplicate ids
        [o(text="   ")],
        [o(text="a\x07b")],
        [o(text="1\n2\n3\n4\n5")],
        [o(start=1.0, end=1.1)],
        [o(style="banner")],
        [o(id="Bad id")],
        [o(text="x" * 121)],
        [o(id=f"i{k}") for k in range(21)],
    ]
    for overlays in bad:
        assert _patch(client, clip, overlays).status_code == 422, overlays

    good = [o(text="Line one\r\nLine two"), o(id="b2", style="label", text="Me", start=1.5, end=4)]
    r = _patch(client, clip, good)
    assert r.status_code == 200, r.json()
    saved = r.json()["edit"]["overlays"]
    assert saved[0]["text"] == "Line one\nLine two" and saved[1]["style"] == "label"

    styles = client.get("/api/v1/overlay-styles").json()["items"]
    assert [s["id"] for s in styles] == ["title", "callout", "label"] and "font_family" in styles[0]
    r = client.post("/api/v1/overlays/layout", json={"overlays": saved, "duration": 3.0})
    assert r.status_code == 200 and r.json()["items"] == ov.layout_all(saved, 3.0)
    assert r.json()["items"][1]["e"] == 3.0
    r = client.post("/api/v1/overlays/layout", json={"overlays": [o(text="")], "duration": 3})
    assert r.status_code == 422


# ------------------------------------------------------------------ rendered glyphs vs preview maths


@pytest.mark.parametrize("style_id", sorted(ov.STYLES))
def test_rendered_overlay_matches_preview_position(style_id: str, tmp_path: Path) -> None:
    """Horizontal centre and baseline of the rendered text = where the preview draws it (within 3 px)."""
    sp = ov.STYLES[style_id]
    plain = Preset(**{**sp.__dict__, "outline": 0, "shadow": 0, "box": None, "color": "#FFFFFF"})
    lo = ov.layout(o(style=style_id, text="HHHH", x=0.3, y=0.4), 2.0)
    assert lo is not None
    styles = dict(ov.STYLES)
    ov.STYLES[style_id] = plain  # measure glyph ink only (no box/outline)
    try:
        doc = ass.build([], plain, None, 2.0, [lo], captions=False)
    finally:
        ov.STYLES.clear()
        ov.STYLES.update(styles)
    (tmp_path / "captions.ass").write_text(doc, encoding="utf-8")
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / sp.font_file).write_bytes((fontinfo.FONTS_DIR / sp.font_file).read_bytes())
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=1080x1920:d=1",
         "-vf", "ass=captions.ass:fontsdir=fonts", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True, cwd=tmp_path,
    )  # fmt: skip
    a = np.frombuffer(r.stdout, np.uint8).reshape(1920, 1080)
    ys = np.where((a > 128).any(axis=1))[0]
    xs = np.where((a > 128).any(axis=0))[0]
    f = sp.font
    # the preview centres each line box on (x, y); the browser puts the baseline at y + (typo asc - desc) / 2
    baseline = lo["ys"][0] + (f.typo_ascent - f.typo_descent) / 2 * sp.size / f.units_per_em
    assert abs((ys.max() + 1) - baseline) <= 3
    assert abs((xs.min() + xs.max() + 1) / 2 - lo["x"]) <= 3


# ------------------------------------------------------------------ export


def test_export_with_overlays_and_no_captions(
    client: TestClient,  # noqa: F811
    ready: dict[str, Any],  # noqa: F811
    tmp_path: Path,
) -> None:
    r = client.post(f"/api/v1/assets/{ready['id']}/clips", json={"start_ms": 1000, "end_ms": 5000})
    clip = r.json()
    edit = {**clip["edit"], "captions": {**clip["edit"]["captions"], "enabled": False}}
    edit["framing"] = {"mode": "crop", "x": 0.0, "y": 0.5, "zoom": 1.0}  # all red
    edit["overlays"] = [o(text="Chapter one", start=0.5, end=2.0, y=0.25)]
    r = client.patch(f"/api/v1/clips/{clip['id']}", json={"edit": edit}, headers={"if-match": '"1"'})
    assert r.status_code == 200, r.json()

    e = export(client, clip["id"], quality="standard")
    assert e["status"] == "done", e
    lo = ov.layout(edit["overlays"][0], 4.0)
    assert lo is not None
    y = int(lo["ys"][0])
    band = slice(y - 40, y + 40)
    path = tmp_path / "clip.mp4"
    path.write_bytes(client.get(f"/api/v1/exports/{e['id']}/files/mp4").content)

    sp = ov.STYLES["title"]
    text_w = fontinfo.metrics(sp.font_file).width(lo["lines"][0], sp.size)
    pad_x = int(lo["x"] - text_w / 2 - sp.box_padding / 2)

    def white_pixels(t: float) -> int:
        f = frame(path, t)[band]
        return int((f > 220).all(axis=-1).sum())

    def box_dark(t: float) -> bool:
        px = frame(path, t)[y, pad_x]  # in the box padding left of the text, over red video
        return bool(px[0] < 90)

    assert white_pixels(1.2) > 2000 and box_dark(1.2)  # shown between 0.5 and 2.0 s
    assert white_pixels(3.0) == 0 and not box_dark(3.0)  # gone afterwards
    manifest = json.loads(client.get(f"/api/v1/exports/{e['id']}/files/json").content.decode("utf-8"))
    assert manifest["overlays"] == [{"style": "title", "text": "CHAPTER ONE", "start_s": 0.5, "end_s": 2.0}]
    assert manifest["captions"]["burned_in"] is False
