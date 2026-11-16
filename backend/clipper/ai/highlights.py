"""Explainable heuristic highlight finder. Pure functions: no I/O, no FFmpeg, no model.

Two modes, chosen per video:

* ``speech`` (enough talking, e.g. podcasts, commentary): candidates are runs of whole sentences, so a clip
  never starts or ends mid-sentence. Each candidate gets named features (hook, specifics, filler, ...) with
  fixed weights; the reasons shown to the user come from the strongest features.
* ``audio`` (little speech, e.g. gameplay without commentary): candidates are windows scored on loudness and
  visual pace. Edges snap to shot cuts where possible.

What this is *not*: it does not understand humour, story or visuals. It ranks moments that are self-contained,
dense and energetic; a human should review the suggestions. The keyword features (hook phrases, filler words)
are English-only; for other languages they are switched off and the scorer relies on timing, loudness and
pace.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- parameters


@dataclass(frozen=True)
class Params:
    min_s: float = 15.0
    max_s: float = 60.0
    count: int = 10

    def __post_init__(self) -> None:
        if not (1.0 <= self.min_s < self.max_s <= 600.0):
            raise ValueError("need 1 <= min_s < max_s <= 600")
        if not (1 <= self.count <= 100):
            raise ValueError("count must be 1..100")


@dataclass
class Candidate:
    start: float
    end: float
    score: float
    features: dict[str, float]
    reasons: list[str]
    text: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_json(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "score": round(self.score, 3),
            "features": {k: round(v, 3) for k, v in self.features.items()},
            "reasons": self.reasons,
            "text": self.text,
        }


@dataclass
class Result:
    mode: str  # "speech" | "audio"
    candidates: list[Candidate]
    notes: list[str] = field(default_factory=list)
    considered: int = 0


# ---------------------------------------------------------------- signals


class Energy:
    """Loudness lookup over waveform peaks (0..255 at `rate` per second), normalised per video."""

    def __init__(self, peaks: Sequence[int], rate: float) -> None:
        self.rate = rate
        self.prefix = [0.0]
        for p in peaks:
            self.prefix.append(self.prefix[-1] + p)
        srt = sorted(peaks)
        self.ok = bool(srt) and rate > 0 and srt[-1] > 0
        self.median = float(srt[len(srt) // 2]) if srt else 0.0
        self.top = float(srt[min(len(srt) - 1, int(len(srt) * 0.99))]) if srt else 0.0  # 99th percentile

    def mean(self, a: float, b: float) -> float:
        n = len(self.prefix) - 1
        i, j = max(0, min(n, int(a * self.rate))), max(0, min(n, math.ceil(b * self.rate)))
        return (self.prefix[j] - self.prefix[i]) / (j - i) if j > i else 0.0

    def level(self, a: float, b: float) -> float:
        """0 = typical loudness for this video or quieter, 1 = as loud as its loudest 1%.

        (The 99th percentile, not the 95th: a single short burst - the one exciting moment in a long, calm
        video - must still register.)"""
        if not self.ok or self.top <= self.median:
            return 0.0
        return max(0.0, min(1.0, (self.mean(a, b) - self.median) / (self.top - self.median)))


def cuts_between(cuts: Sequence[float], a: float, b: float) -> int:
    return bisect_left(cuts, b) - bisect_right(cuts, a)


def pace(cuts: Sequence[float], a: float, b: float) -> float:
    """Shot cuts per 10 s, mapped to 0..1 (4+ cuts per 10 s = 1)."""
    return min(1.0, cuts_between(cuts, a, b) / max(1e-6, b - a) * 10 / 4)


def snap(t: float, cuts: Sequence[float], lo: float, hi: float) -> float:
    """Nearest cut inside [lo, hi], else t, so clips start/end on a shot change instead of a frame later."""
    i, j = bisect_left(cuts, lo), bisect_right(cuts, hi)
    if i >= j:
        return t
    return min(cuts[i:j], key=lambda c: abs(c - t))


# ---------------------------------------------------------------- speech mode

PAUSE_SPLIT = 0.45  # s of silence that ends a sentence even without punctuation
MAX_SENTENCE = 10.0  # s; longer runs are split at their largest internal pause
TOPIC_GAP = 1.5  # s; a pause this long inside a candidate suggests two topics
END_PAUSE = 1.0  # s; a pause this long after a candidate means the thought is finished

STOP = set(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "so",
        "to",
        "of",
        "in",
        "on",
        "for",
        "is",
        "are",
        "was",
        "were",
        "it",
        "that",
        "this",
        "we",
        "you",
        "i",
        "they",
        "he",
        "she",
        "be",
        "with",
        "at",
        "as",
        "by",
        "do",
        "did",
        "not",
        "have",
        "has",
        "just",
    ]
)
FILLERS = re.compile(
    r"\b(um+|uh+|erm|(?<!did )you know(?! that)|i mean|what was i going to say|where was i|anyway)\b", re.I
)
DANGLING = re.compile(
    r"^(and|so|but|then|because|which|also|anyway|right|sure|exactly|okay|or)\b"
    r"|^(he|she|they|it|that|this|those|these|him|her|them)\b",
    re.I,
)
HOOK = re.compile(
    r"\?|^(here is|here's|did you know|the (most|biggest|best|worst|one|real)|never|always|why|how|what"
    r"|stop|imagine|nobody|everyone)\b|\b(secret|mistake|lesson|important|surprising|myth|tip|rule"
    r"|truth|crazy|insane)\b",
    re.I,
)
NUM = re.compile(
    r"\b(\d[\d,.]*%?|one|two|three|four|five|six|seven|eight|nine|ten|twenty|hundred|thousand|million"
    r"|billion|percent|minutes?|hours?|days?|weeks?|months?|years?)\b",
    re.I,
)
TOKEN = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)

WEIGHTS = {
    "topic_break": 2.0,
    "hook": 2.0,
    "self_contained": 1.5,
    "filler": 1.5,
    "specifics": 1.0,
    "question_payoff": 1.0,
    "ending": 1.0,
    "density": 0.5,
    "salience": 0.5,
    "energy": 0.75,
    "pace": 0.25,
}
WHY = {
    "hook": "strong opening line",
    "specifics": "concrete facts or numbers",
    "question_payoff": "asks a question and answers it",
    "self_contained": "starts mid-thought",
    "filler": "contains filler words",
    "topic_break": "spans a long pause (may be two topics)",
    "ending": "ends on a complete point",
    "energy": "lively, louder delivery",
    "pace": "fast visual cuts",
}
ENGLISH_ONLY = {"hook", "self_contained", "filler", "specifics", "salience"}


@dataclass
class Sentence:
    words: list[dict[str, Any]]

    @property
    def s(self) -> float:
        return float(self.words[0]["s"])

    @property
    def e(self) -> float:
        return float(self.words[-1]["e"])

    @property
    def text(self) -> str:
        return " ".join(str(w["w"]) for w in self.words)


def sentences(words: Sequence[dict[str, Any]]) -> list[Sentence]:
    """Split on end punctuation or pauses; hard-cap length by splitting at the largest internal pause."""
    out: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for k, w in enumerate(words):
        cur.append(w)
        gap = float(words[k + 1]["s"]) - float(w["e"]) if k + 1 < len(words) else math.inf
        if (
            re.search(r"[.?!。？！]$", str(w["w"]))  # noqa: RUF001 - CJK sentence ends
            or gap >= PAUSE_SPLIT
            or (len(cur) >= 40 and gap >= 0.2)
        ):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)

    def split_long(s: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        if float(s[-1]["e"]) - float(s[0]["s"]) <= MAX_SENTENCE or len(s) < 4:
            return [s]
        k = max(range(1, len(s)), key=lambda q: float(s[q]["s"]) - float(s[q - 1]["e"]))
        return split_long(s[:k]) + split_long(s[k:])

    return [Sentence(p) for s in out for p in split_long(s)]


class _SpeechScorer:
    def __init__(self, sents: list[Sentence], english: bool, energy: Energy, cuts: Sequence[float]) -> None:
        self.S, self.english, self.energy, self.cuts = sents, english, energy, cuts
        self.toks = [[t for t in TOKEN.findall(s.text.lower())] for s in sents]
        self.tf = Counter(t for ts in self.toks for t in ts if t not in STOP and len(t) > 3)

    def _ending(self, j: int) -> float:
        """1 = the thought is finished: no open question and the speaker pauses afterwards.

        Without the pause test, stopping mid-paragraph scored the same as stopping at its end (found on a real
        transcript in the M3 smoke test), and the shorter, cut-off clip won the tie."""
        if self.S[j].text.rstrip().endswith("?"):
            return 0.0
        next_gap = self.S[j + 1].s - self.S[j].e if j + 1 < len(self.S) else math.inf
        return 1.0 if next_gap >= END_PAUSE else 0.3

    def score(self, i: int, j: int) -> tuple[float, dict[str, float]]:
        c = self.S[i : j + 1]
        first, text = c[0].text, " ".join(s.text for s in c)
        a, b = c[0].s, c[-1].e
        dur = max(1e-6, b - a)
        toks = [t for ts in self.toks[i : j + 1] for t in ts]
        gaps = [c[k + 1].s - c[k].e for k in range(len(c) - 1)]
        f = {
            "topic_break": -1.0 if any(g > TOPIC_GAP for g in gaps) else 0.0,
            "question_payoff": 1.0 if any(s.text.rstrip().endswith("?") for s in c[:-1]) else 0.0,
            "ending": self._ending(j),
            "density": min(1.0, (len(toks) / dur) / 2.8),  # words/s; 2.8 ~ brisk speech
            "energy": self.energy.level(a, b),
            "pace": pace(self.cuts, a, b),
        }
        if self.english:
            f["hook"] = 1.0 if HOOK.search(first) and not FILLERS.search(first) else 0.0
            f["self_contained"] = -1.0 if DANGLING.search(first) else 0.0
            f["filler"] = -min(1.0, len(FILLERS.findall(text)) / 2)
            f["specifics"] = min(1.0, len(NUM.findall(text)) / 2)
            sal = sum(math.log1p(self.tf[t]) for t in toks if t in self.tf) / max(1, len(toks))
            f["salience"] = min(1.0, sal / 0.6)
        return sum(WEIGHTS[k] * v for k, v in f.items()), f


def _reasons(f: dict[str, float]) -> list[str]:
    ranked = sorted(f.items(), key=lambda kv: -abs(WEIGHTS.get(kv[0], 1.0) * kv[1]))
    return [WHY[k] for k, v in ranked if k in WHY and abs(v) >= 0.5][:3]


def _speech(
    words: Sequence[dict[str, Any]],
    duration: float,
    english: bool,
    energy: Energy,
    cuts: Sequence[float],
    p: Params,
) -> tuple[list[Candidate], int]:
    S = sentences(words)
    sc = _SpeechScorer(S, english, energy, cuts)
    cands: list[Candidate] = []
    for i in range(len(S)):
        for j in range(i, len(S)):
            dur = S[j].e - S[i].s
            if dur > p.max_s:
                break
            if dur < p.min_s:
                continue
            total, f = sc.score(i, j)
            # pad edges a little (breath before, trailing sound after) without touching neighbouring words,
            # then snap to a shot cut if one is very close
            prev_end = S[i - 1].e if i > 0 else 0.0
            next_start = S[j + 1].s if j + 1 < len(S) else duration
            a = max(prev_end, S[i].s - 0.15, 0.0)
            b = min(next_start, S[j].e + 0.35, duration)
            a = snap(a, cuts, max(prev_end, S[i].s - 0.5), S[i].s)
            b = snap(b, cuts, S[j].e, min(next_start, S[j].e + 0.6))
            if b - a > p.max_s:  # padding must never push a clip over the requested maximum
                b = max(S[j].e, a + p.max_s)
                a = max(a, b - p.max_s)
            text = " ".join(s.text for s in S[i : j + 1])
            cands.append(Candidate(a, b, total, f, _reasons(f), text))
    return cands, len(cands)


# ---------------------------------------------------------------- audio mode


def _audio(duration: float, energy: Energy, cuts: Sequence[float], p: Params) -> tuple[list[Candidate], int]:
    if not energy.ok and not cuts:
        return [], 0
    top = min(p.max_s, duration)
    lengths = sorted({round(p.min_s + (top - p.min_s) * k / 4, 2) for k in range(5)})  # 5 window sizes
    cands: list[Candidate] = []
    step = 1.0
    for length in lengths:
        t = 0.0
        while t + length <= duration + 1e-6:
            a = snap(t, cuts, t - 1.0, t + 1.0)
            b = min(duration, a + length)
            b = snap(b, cuts, b - 1.0, min(duration, b + 1.0))
            if p.min_s <= b - a <= p.max_s:
                lvl = energy.level(a, b)
                # a peak late in the window ("build-up then payoff") is worth more than a flat loud stretch
                half = a + (b - a) / 2
                build = max(0.0, energy.level(half, b) - energy.level(a, half))
                # ending while it is still loud cuts the moment off before its payoff
                mid_action = (
                    b < duration - 0.5 and min(energy.level(b - 1.0, b), energy.level(b, b + 1.5)) >= 0.5
                )
                f = {"energy": lvl, "build_up": min(1.0, build * 2), "pace": pace(cuts, a, b),
                     "cut_off": -1.0 if mid_action else 0.0}  # fmt: skip
                total = 2.0 * f["energy"] + 1.0 * f["build_up"] + 1.0 * f["pace"] + 1.5 * f["cut_off"]
                reasons = [
                    r
                    for r, k in (
                        ("loud, intense moment", "energy"),
                        ("builds to a peak", "build_up"),
                        ("fast visual cuts", "pace"),
                    )
                    if f[k] >= 0.5
                ]
                cands.append(Candidate(a, b, total, f, reasons))
            t += step
    return cands, len(cands)


# ---------------------------------------------------------------- entry point


def pick(cands: list[Candidate], count: int) -> list[Candidate]:
    """Highest scores first, never overlapping; ties prefer the earlier, shorter clip (deterministic)."""
    cands = sorted(cands, key=lambda c: (-c.score, c.start, c.duration))
    picked: list[Candidate] = []
    for c in cands:
        if all(c.end <= q.start or c.start >= q.end for q in picked):
            picked.append(c)
            if len(picked) == count:
                break
    return picked


def find_highlights(
    words: Sequence[dict[str, Any]],
    *,
    duration: float,
    language: str | None,
    cuts: Sequence[float] = (),
    peaks: Sequence[int] = (),
    peaks_rate: float = 0.0,
    params: Params | None = None,
) -> Result:
    p = params or Params()
    cuts = sorted(cuts)
    energy = Energy(peaks, peaks_rate)
    notes: list[str] = []
    if duration < p.min_s:
        return Result("speech", [], [f"video is shorter than the minimum clip length ({p.min_s:g} s)"])
    speech_s = sum(max(0.0, float(w["e"]) - float(w["s"])) for w in words)
    english = (language or "").lower().startswith("en")
    if speech_s >= 0.15 * duration and len(words) >= 20:
        if not english:
            notes.append("keyword features are English-only; ranking uses timing, loudness and pace")
        cands, n = _speech(words, duration, english, energy, cuts, p)
        mode = "speech"
        if not cands:
            notes.append("no run of complete sentences fits the clip length; try other min/max lengths")
    else:
        notes.append("little or no speech found; ranking by loudness and visual pace only")
        cands, n = _audio(duration, energy, cuts, p)
        mode = "audio"
    # never pad the list with moments the scorer itself rates as poor
    picked = pick([c for c in cands if c.score > 0], p.count)
    if cands and len(picked) < p.count:
        notes.append(f"only {len(picked)} of {p.count} requested moments scored well enough to suggest")
    return Result(mode, picked, notes, n)
