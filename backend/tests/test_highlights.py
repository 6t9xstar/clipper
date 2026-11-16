"""Unit tests for the heuristic highlight finder (pure functions, synthetic inputs with known answers)."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from clipper.ai import highlights as hl


def speak(text: str, start: float, wps: float = 2.8) -> tuple[list[dict[str, Any]], float]:
    """Word timings for `text` spoken at `wps` words/s from `start`; returns (words, end time)."""
    out = []
    t = start
    for w in text.split():
        out.append({"w": w, "s": round(t, 3), "e": round(t + 0.8 / wps, 3), "p": 0.9})
        t += 1 / wps
    return out, t


STRONG = (
    "Why do most new runners get injured in the first three weeks? "
    "Here's the mistake. They increase distance by thirty percent every week. "
    "The rule that works is ten percent per week, and your knees will thank you for it. "
    "That one change cut injuries in half in our club last year."
)
WEAK = (
    "And so um anyway that was kind of the thing I mean you know. "
    "So yeah uh we went there and it was um fine I guess and then we left. "
    "And they said something about it but I mean uh I don't really remember it. "
    "So um anyway yeah that was that I mean."
)


def transcript(order: list[str], gap: float = 2.5) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    t = 0.5
    for kind in order:
        ws, t = speak(STRONG if kind == "S" else WEAK, t)
        words += ws
        t += gap
    return words


def test_params_validation() -> None:
    with pytest.raises(ValueError):
        hl.Params(min_s=30, max_s=20)
    with pytest.raises(ValueError):
        hl.Params(count=0)


def test_sentences_split_on_punctuation_pause_and_length() -> None:
    ws, _ = speak("Hello there. How are you? Fine", 0)
    assert [s.text for s in hl.sentences(ws)] == ["Hello there.", "How are you?", "Fine"]
    a, t = speak("no punctuation here", 0)
    b, _ = speak("after a pause", t + 1.0)
    assert len(hl.sentences(a + b)) == 2
    long_run, _ = speak(" ".join(["word"] * 80), 0)  # ~28 s without punctuation or pauses
    parts = hl.sentences(long_run)
    assert len(parts) > 1 and all(p.e - p.s <= hl.MAX_SENTENCE for p in parts)


def test_strong_segment_ranks_first_and_clips_respect_sentences() -> None:
    words = transcript(["W", "W", "S", "W", "W"])
    duration = words[-1]["e"] + 1
    r = hl.find_highlights(
        words, duration=duration, language="en", params=hl.Params(min_s=10, max_s=30, count=3)
    )
    assert r.mode == "speech" and r.candidates
    best = r.candidates[0]
    assert best.text.startswith("Why do most new runners")
    assert "strong opening line" in best.reasons
    # every clip edge lies outside a word (never mid-word) and within limits
    for c in r.candidates:
        assert 10 - 0.01 <= c.duration <= 30 + 1e-6
        assert all(not (w["s"] < c.start < w["e"]) and not (w["s"] < c.end < w["e"]) for w in words)
    # no overlaps, sorted by score
    cs = sorted(r.candidates, key=lambda c: c.start)
    assert all(a.end <= b.start for a, b in itertools.pairwise(cs))
    assert [c.score for c in r.candidates] == sorted((c.score for c in r.candidates), reverse=True)


def test_weak_segments_get_negative_features() -> None:
    weak_words, t = speak(WEAK, 0)
    n_weak = len(hl.sentences(weak_words))
    S = hl.sentences(weak_words + speak(STRONG, t + 2.5)[0])
    sc = hl._SpeechScorer(S, english=True, energy=hl.Energy([], 0), cuts=[])
    weak_total, weak = sc.score(0, n_weak - 1)
    strong_total, strong = sc.score(n_weak, len(S) - 1)
    assert weak["filler"] < 0 and weak["self_contained"] < 0 and weak["hook"] == 0
    assert strong["hook"] == 1 and strong["specifics"] > 0 and strong["question_payoff"] == 1
    assert strong_total > weak_total


def test_non_english_disables_keyword_features() -> None:
    words = transcript(["W", "S", "W"])
    r = hl.find_highlights(words, duration=words[-1]["e"] + 1, language="ur", params=hl.Params(10, 30, 3))
    assert any("English-only" in n for n in r.notes)
    assert all("hook" not in c.features and "filler" not in c.features for c in r.candidates)


def test_cut_snapping_and_max_length_clamp() -> None:
    words = transcript(["S"])
    first = words[0]["s"]
    cut = first - 0.3  # a shot change just before speech starts
    r = hl.find_highlights(words, duration=words[-1]["e"] + 2, language="en", cuts=[cut],
                           params=hl.Params(min_s=5, max_s=40, count=1))  # fmt: skip
    assert r.candidates[0].start == pytest.approx(cut)
    assert hl.snap(10.0, [3.0, 9.8, 20.0], 9.5, 10.0) == 9.8
    assert hl.snap(10.0, [3.0, 20.0], 9.5, 10.0) == 10.0


def test_audio_mode_finds_loud_burst() -> None:
    rate, duration = 20, 300.0
    peaks = [30] * int(duration * rate)
    for k in range(100 * rate, 112 * rate):  # loud stretch 100-112 s
        peaks[k] = 220
    r = hl.find_highlights([], duration=duration, language=None, peaks=peaks, peaks_rate=rate,
                           cuts=[99.0, 113.0], params=hl.Params(min_s=10, max_s=30, count=3))  # fmt: skip
    assert r.mode == "audio" and any("speech" in n for n in r.notes)
    top = r.candidates[0]
    assert top.start <= 100 and top.end >= 112
    assert "loud, intense moment" in top.reasons


def test_energy_normalisation() -> None:
    e = hl.Energy([10] * 90 + [200] * 10, rate=10)
    assert e.level(0, 5) == 0.0
    assert e.level(9, 10) == pytest.approx(1.0)
    assert not hl.Energy([], 0).ok and hl.Energy([], 0).level(0, 1) == 0.0


def test_too_short_video_and_silence() -> None:
    assert hl.find_highlights([], duration=5, language=None).candidates == []
    r = hl.find_highlights([], duration=120, language=None, params=hl.Params(10, 30, 3))
    assert r.candidates == [] and r.mode == "audio"


def test_deterministic() -> None:
    words = transcript(["W", "S", "W", "S"])
    kw: dict[str, Any] = {"duration": words[-1]["e"] + 1, "language": "en", "params": hl.Params(8, 40, 4)}
    a = [c.to_json() for c in hl.find_highlights(words, **kw).candidates]
    b = [c.to_json() for c in hl.find_highlights(words, **kw).candidates]
    assert a == b


def test_prefers_whole_thought_over_cut_off_and_drops_poor_moments() -> None:
    """Regression from the M3 smoke test: a paragraph followed by a long pause must be suggested whole,
    and poor (negative-score) moments must not be used to fill the requested count."""
    words = transcript(["W", "S", "W"], gap=3.0)
    r = hl.find_highlights(words, duration=words[-1]["e"] + 1, language="en", params=hl.Params(8, 30, 5))
    strong_end = speak(STRONG, 0.5 + len(WEAK.split()) / 2.8 + 3.0)[0][-1]["e"]
    best = r.candidates[0]
    assert best.text.startswith("Why do most") and best.text.endswith("last year.")
    assert best.end >= strong_end
    assert all(c.score > 0 for c in r.candidates)
    assert len(r.candidates) < 5 and any("scored well enough" in n for n in r.notes)
