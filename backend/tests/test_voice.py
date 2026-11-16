"""M6 voiceover: speech detection, placement and ducking maths (shared vectors with the preview), the voice
API, and exports whose audio is measured: the original is ducked while the voice speaks, muted on request."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from clipper.api.app import create_app
from clipper.core.config import Settings
from clipper.edit import voice as vo
from clipper.media import voice as mv

from . import media_corpus
from .test_analyze import calls  # noqa: F401  (fixture)
from .test_import import drain, import_file, pytestmark  # noqa: F401  (needs ffmpeg)
from .test_render import export, halves  # noqa: F401  (fixture)
from .util import authed

CASES = Path(__file__).resolve().parents[2] / "testdata" / "ducking_cases.json"

# ------------------------------------------------------------------ pure maths


def test_speech_segments_bridge_short_gaps_and_drop_blips() -> None:
    lv = [-80.0] * 200
    for i in range(25, 75):  # 0.5 - 1.5 s speech
        lv[i] = -20.0
    for i in (40, 41, 42):  # 60 ms dip inside a word: bridged
        lv[i] = -70.0
    lv[120] = -20.0  # a 20 ms click: dropped
    for i in range(150, 160):  # 3.0 - 3.2 s short word
        lv[i] = -24.0
    assert mv.speech_segments(lv) == [[0.5, 1.5], [3.0, 3.2]]
    assert mv.speech_segments([-90.0] * 50) == []  # silence under the floor


def test_placement_merges_clips_and_covers() -> None:
    take = [[0.0, 1.0], [1.5, 2.0], [4.0, 5.0]]
    segs = vo.placed_segments([(2.0, take)], 6.0)
    assert segs == [(2.0, 4.0)]  # the 0.5 s pause is merged; the part after the clip end is dropped
    assert vo.placed_segments([(-1.0, [[0.0, 2.0]])], 5.0) == [(0.0, 1.0)]
    assert vo.coverage([(0.0, 1.0), (2.0, 3.0)], 4.0) == 0.5
    many = [[i * 0.1, i * 0.1 + 0.01] for i in range(5000)]
    assert len(vo.placed_segments([(0.0, many)], 600.0)) <= vo.MAX_SEGMENTS


def test_duck_gain_matches_shared_vectors() -> None:
    """The same vectors are checked by the preview's TypeScript (frontend/src/lib/lib.test.ts)."""
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    for c in cases:
        segs = [tuple(x) for x in c["segments"]]
        for t, want in c["samples"]:
            assert abs(vo.duck_gain(t, segs, c["duck_db"]) - want) < 1e-6, (c, t)
    assert vo.duck_expr([], -12) is None and vo.duck_expr([(1, 2)], 0) is None
    e = vo.duck_expr([(0.05, 1.0), (2.0, 3.0), (4.0, 5.0)], -12)
    assert e is not None and e.count("max(") == 2 and "(t-(-0.070))" in e


# ------------------------------------------------------------------ API and export


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    st = settings.model_copy(update={"asr_model": "tiny", "asr_device": "cpu"})
    with authed(create_app(st)) as c:
        yield c


@pytest.fixture(scope="module")
def voice_wav(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """4 s: a 1 kHz 'voice' speaking 0.5-1.5 s and 2.5-3.5 s, silence otherwise."""
    out = tmp_path_factory.mktemp("voice") / "voice.wav"
    media_corpus._ff("-f", "lavfi", "-i",
                     "sine=f=1000:d=4,volume='0.3*(between(t,0.5,1.5)+between(t,2.5,3.5))':eval=frame",
                     "-c:a", "pcm_s16le", str(out))  # fmt: skip
    return out


def make_clip(c: TestClient, aid: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    r = c.post(f"/api/v1/assets/{aid}/clips", json={"start_ms": start_ms, "end_ms": end_ms})
    assert r.status_code == 201, r.json()
    out: dict[str, Any] = r.json()
    return out


def upload(c: TestClient, clip_id: str, data: bytes, ctype: str = "audio/wav") -> Any:
    return c.post(f"/api/v1/clips/{clip_id}/voice?name=Intro", content=data, headers={"content-type": ctype})


def patch_edit(c: TestClient, clip: dict[str, Any], **parts: Any) -> Any:
    edit = {**clip["edit"], **parts}
    return c.patch(
        f"/api/v1/clips/{clip['id']}", json={"edit": edit}, headers={"if-match": f'"{clip["version"]}"'}
    )


def band_rms(path: Path, freq: int, t0: float, t1: float) -> float:
    """Level (dB, relative) of one frequency of the export's audio between t0 and t1: FFT power within
    +-15 Hz, so a loud voice at another frequency cannot leak into the measurement."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
        capture_output=True, check=True,
    )  # fmt: skip
    a = np.frombuffer(r.stdout, np.float32)[int(t0 * 8000) : int(t1 * 8000)].astype(np.float64)
    spec = (np.abs(np.fft.rfft(a * np.hanning(len(a)))) / len(a)) ** 2  # tone power independent of length
    hz = np.fft.rfftfreq(len(a), 1 / 8000)
    return float(10 * np.log10(spec[(hz > freq - 15) & (hz < freq + 15)].sum() + 1e-12))


def test_voice_api(client: TestClient, halves: Path, voice_wav: Path) -> None:  # noqa: F811
    a = import_file(client, halves)
    clip = make_clip(client, a["id"], 1000, 7000)
    other = make_clip(client, a["id"], 2000, 5000)

    assert upload(client, clip["id"], b"hello", "text/plain").status_code == 415
    r = upload(client, clip["id"], b"not audio at all" * 100)
    assert r.status_code == 422 and r.json()["code"] == "VOICE_UNREADABLE"
    r = upload(client, clip["id"], voice_wav.read_bytes())
    assert r.status_code == 201, r.json()
    t = r.json()
    assert t["name"] == "Intro" and abs(t["duration_ms"] - 4000) <= 30 and t["loudness_lufs"] is not None
    assert len(t["segments"]) == 2 and all(
        abs(s - e) <= 0.06 for seg, exp in zip(t["segments"], [[0.5, 1.5], [2.5, 3.5]], strict=True)
        for s, e in zip(seg, exp, strict=True)
    )  # fmt: skip
    assert len(t["peaks"]) == 80  # 20 per second

    assert [x["id"] for x in client.get(f"/api/v1/clips/{clip['id']}/voice").json()["items"]] == [t["id"]]
    r = client.get(f"/api/v1/voice/{t['id']}/audio")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/flac" and r.content[:4] == b"fLaC"

    # a take can only be placed in its own clip
    r = patch_edit(client, other, voiceovers=[{"track_id": t["id"], "start": 0}])
    assert r.status_code == 422 and r.json()["code"] == "VOICE_NOT_FOUND"
    r = patch_edit(client, clip, voiceovers=[{"track_id": t["id"], "start": 0.5}])
    assert r.status_code == 200, r.json()
    assert client.delete(f"/api/v1/voice/{t['id']}").json()["code"] == "VOICE_IN_USE"

    t2 = upload(client, clip["id"], voice_wav.read_bytes()).json()
    assert t2["name"] == "Intro"
    assert client.delete(f"/api/v1/voice/{t2['id']}").status_code == 204
    assert client.get(f"/api/v1/voice/{t2['id']}/audio").status_code == 404

    # deleting the clip removes its takes and their files
    data_dir = Path(client.app.state.clipper.settings.data_dir)  # type: ignore[attr-defined]
    assert list(data_dir.rglob(f"{t['id']}/voice.flac"))
    assert client.delete(f"/api/v1/clips/{clip['id']}").status_code == 204
    assert not list(data_dir.rglob(f"{t['id']}/voice.flac"))


def test_export_ducks_the_original_under_the_voice(
    client: TestClient,
    halves: Path,  # noqa: F811
    voice_wav: Path,
    tmp_path: Path,
) -> None:
    a = import_file(client, halves)  # 440 Hz tone throughout
    clip = make_clip(client, a["id"], 2000, 8000)  # 6 s
    t = upload(client, clip["id"], voice_wav.read_bytes()).json()
    levels = {"original_db": 0, "mute_original": False, "voice_db": 0, "duck_db": -12}
    r = patch_edit(client, clip, voiceovers=[{"track_id": t["id"], "start": 1.0}], audio=levels)
    assert r.status_code == 200, r.json()
    e = export(client, clip["id"], quality="draft")
    assert e["status"] == "done", e
    assert all(c["ok"] for k, c in e["checks"].items() if k != "loudness"), e["checks"]
    mp4 = tmp_path / "o.mp4"
    mp4.write_bytes(client.get(f"/api/v1/exports/{e['id']}/files/mp4").content)

    # voice speaks at clip 1.5-2.5 s and 3.5-4.5 s
    before = band_rms(mp4, 440, 0.2, 1.2)
    during = band_rms(mp4, 440, 1.8, 2.3)
    after = band_rms(mp4, 440, 5.2, 5.9)
    assert abs((before - during) - 12) <= 2.0, (before, during)  # ducked by ~12 dB
    assert abs(before - after) <= 1.0  # and back up afterwards
    assert band_rms(mp4, 1000, 1.8, 2.3) > band_rms(mp4, 1000, 0.2, 1.2) + 20  # the voice is there

    m = json.loads(client.get(f"/api/v1/exports/{e['id']}/files/json").content)
    assert m["voiceover"]["takes"][0]["start_s"] == 1.0 and abs(m["voiceover"]["coverage"] - 2 / 6) < 0.02

    # muted original: only the voice remains
    clip = client.get(f"/api/v1/clips/{clip['id']}").json()
    r = patch_edit(client, clip, audio={**clip["edit"]["audio"], "mute_original": True})
    assert r.status_code == 200
    e = export(client, clip["id"], quality="draft")
    assert e["status"] == "done", e
    mp4.write_bytes(client.get(f"/api/v1/exports/{e['id']}/files/mp4").content)
    assert band_rms(mp4, 440, 0.2, 1.2) < -60
