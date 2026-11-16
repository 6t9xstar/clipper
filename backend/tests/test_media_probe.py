"""Unit tests for clipper.media.probe.analyse (pure: synthetic ffprobe JSON, no FFmpeg needed)."""

from __future__ import annotations

from typing import Any

import pytest

from clipper.media.errors import MediaError
from clipper.media.prepare import proxy_size, thumb_spec
from clipper.media.probe import analyse


def probe_json(**over: Any) -> dict[str, Any]:
    v: dict[str, Any] = {
        "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
        "avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001", "pix_fmt": "yuv420p",
    }  # fmt: skip
    a: dict[str, Any] = {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"}
    fmt: dict[str, Any] = {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "125.5",
        "bit_rate": "5000000",
    }
    v.update(over.pop("video", {}))
    fmt.update(over.pop("format", {}))
    streams = over.pop("streams", [v, a])
    return {"format": fmt, "streams": streams}


def code(data: dict[str, Any], **kw: Any) -> str:
    with pytest.raises(MediaError) as e:
        analyse(data, max_duration_s=kw.get("max_s", 14400))
    return e.value.code


def test_normal_file() -> None:
    i = analyse(probe_json(), max_duration_s=14400)
    assert (i.width, i.height, i.duration_ms, i.has_audio, i.warnings) == (1920, 1080, 125500, True, [])
    assert i.fps == pytest.approx(29.97, abs=0.01)


def test_rotation_swaps_display_size() -> None:
    d = probe_json(video={"width": 1920, "height": 1080, "side_data_list": [{"rotation": -90}]})
    i = analyse(d, max_duration_s=14400)
    assert (i.width, i.height, i.rotation) == (1080, 1920, 270)
    legacy = probe_json(video={"tags": {"rotate": "90"}})
    assert analyse(legacy, max_duration_s=14400).width == 1080


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (probe_json(streams=[{"codec_type": "audio", "codec_name": "aac"}]), "MEDIA_NO_VIDEO"),
        (probe_json(streams=[{"codec_type": "video", "codec_name": "mjpeg", "width": 600, "height": 600,
                              "avg_frame_rate": "0/0", "disposition": {"attached_pic": 1}}]),
         "MEDIA_NO_VIDEO"),
        (probe_json(video={"codec_name": "qtrle"}), "MEDIA_UNSUPPORTED_CODEC"),
        (probe_json(format={"duration": None}), "MEDIA_UNREADABLE"),
        (probe_json(format={"duration": "nan"}), "MEDIA_UNREADABLE"),
        (probe_json(format={"duration": "0.4"}), "MEDIA_TOO_SHORT"),
        (probe_json(video={"width": 32, "height": 32}), "MEDIA_RESOLUTION_UNSUPPORTED"),
        (probe_json(video={"width": 8192, "height": 4320}), "MEDIA_RESOLUTION_UNSUPPORTED"),
        (probe_json(video={"avg_frame_rate": "0/0", "r_frame_rate": "0/0"}), "MEDIA_UNREADABLE"),
    ],
)  # fmt: skip
def test_rejections(data: dict[str, Any], expected: str) -> None:
    assert code(data) == expected


def test_too_long() -> None:
    assert code(probe_json(format={"duration": "7200.1"}), max_s=7200) == "MEDIA_TOO_LONG"


def test_portrait_8k_allowed_and_warnings() -> None:
    d = probe_json(video={"width": 4320, "height": 7680, "color_transfer": "smpte2084",
                          "pix_fmt": "yuv420p10le", "field_order": "tt",
                          "avg_frame_rate": "240/1", "r_frame_rate": "240/1"})  # fmt: skip
    d["streams"] = d["streams"][:1]
    i = analyse(d, max_duration_s=14400)
    assert i.bit_depth == 10 and i.hdr and i.interlaced
    assert set(i.warnings) == {"NO_AUDIO", "HDR_COLORS_MAY_LOOK_WASHED_OUT", "INTERLACED", "HIGH_FRAME_RATE"}


def test_proxy_and_thumb_geometry() -> None:
    land = analyse(probe_json(), max_duration_s=14400)
    assert proxy_size(land) == (960, 540)
    port = analyse(probe_json(video={"width": 1080, "height": 1920}), max_duration_s=14400)
    assert proxy_size(port) == (540, 960)
    small = analyse(probe_json(video={"width": 320, "height": 240}), max_duration_s=14400)
    assert proxy_size(small) == (320, 240)  # never upscale
    ts = thumb_spec(land)
    assert ts.count == 63 and ts.cols == 10 and ts.rows == 7 and ts.tile_w == 160 and ts.tile_h == 90
    long = analyse(probe_json(format={"duration": "10800"}), max_duration_s=14400)
    assert thumb_spec(long).count == 120
