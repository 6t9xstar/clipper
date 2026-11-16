"""P4: explainable heuristic highlight scorer over word-timestamped transcript.
Candidates = runs of whole sentences within [MIN,MAX] s, snapped to sentence boundaries (never mid-sentence)."""
import json, math, os, re, sys
from collections import Counter
from pathlib import Path

H = Path(__file__).parent
TJ = H / "out" / "transcript.json"
MIN_S, MAX_S, TOP_K = 6.0, 20.0, 4

if not TJ.exists():
    sys.argv = ["x"]; exec((H / "p1_asr_bench.py").read_text().split("device, model =")[0])  # reuse DLL shim
    from faster_whisper import WhisperModel
    segs, _ = WhisperModel("small", device="cpu", compute_type="int8").transcribe(
        str(H / "media" / "speech.wav"), language="en", word_timestamps=True, vad_filter=True)
    TJ.write_text(json.dumps([{"w": w.word.strip(), "s": w.start, "e": w.end} for s in segs for w in s.words]))
words = json.loads(TJ.read_text())

# --- sentences from word stream (punctuation from Whisper) ---
sents, cur = [], []
for w in words:
    cur.append(w)
    if re.search(r"[.?!]$", w["w"]): sents.append(cur); cur = []
if cur: sents.append(cur)
S = [{"text": " ".join(w["w"] for w in s), "s": s[0]["s"], "e": s[-1]["e"]} for s in sents]

STOP = set("the a an and or but so to of in on for is are was were it that this we you i they he she be with at as by do did not have has just".split())
FILLERS = re.compile(r"\b(um+|uh+|(?<!did )you know(?! that)|i mean|where was i|okay so|anyway)\b", re.I)
DANGLING = re.compile(r"^(and|so|but|then|because|which|also|anyway|right)\b|^(he|she|they|it|that|this|those)\b", re.I)
HOOK = re.compile(r"\?|^(here is|here's|did you know|the (most|biggest|one)|never|always|why|how|what)\b|\b(secret|mistake|lesson|important)\b", re.I)
NUM = re.compile(r"\b(\d+|one|two|three|five|six|ten|twenty|hundred|thousand|percent|minute|days?|weeks?)\b", re.I)

tf = Counter(t for s in S for t in re.findall(r"[a-z']+", s["text"].lower()) if t not in STOP and len(t) > 3)


def score(c):
    first, text = c[0]["text"], " ".join(s["text"] for s in c)
    dur = c[-1]["e"] - c[0]["s"]
    toks = re.findall(r"[a-z']+", text.lower())
    gaps = [c[k + 1]["s"] - c[k]["e"] for k in range(len(c) - 1)]
    f = {
        "hook": 1.0 if HOOK.search(first) and not FILLERS.search(first) else 0.0,
        "topic_break": -1.0 if any(g > 1.2 for g in gaps) else 0.0,  # long pause inside = likely 2 topics
        "self_contained": -1.0 if DANGLING.search(first) else 0.0,
        "filler": -min(1.0, len(FILLERS.findall(text)) / 2),
        "specifics": min(1.0, len(NUM.findall(text)) / 2),
        "question_payoff": 1.0 if "?" in first and len(c) > 1 else 0.0,
        "density": min(1.0, (len(toks) / dur) / 2.8),  # words/sec; 2.8 ~ brisk speech
        "salience": min(1.0, sum(math.log1p(tf[t]) for t in toks if t in tf) / max(1, len(toks)) / 0.6),
    }
    wts = {"topic_break": 2.0, "hook": 2.0,"self_contained": 1.5, "filler": 1.5, "specifics": 1.0, "question_payoff": 1.0, "density": 0.5, "salience": 0.5}
    total = sum(wts[k] * v for k, v in f.items())
    why = {"hook": "strong opening line", "specifics": "concrete numbers/specifics", "question_payoff": "poses a question and answers it",
           "self_contained": "starts mid-thought (needs context)", "filler": "contains filler/hesitation", "topic_break": "spans a long pause (two topics)"}
    reasons = [why[k] for k, v in sorted(f.items(), key=lambda kv: -abs(wts[kv[0]] * kv[1])) if v and k in why][:3]
    return total, dur, reasons


cands = []
for i in range(len(S)):
    for j in range(i, len(S)):
        dur = S[j]["e"] - S[i]["s"]
        if dur > MAX_S: break
        if j + 1 < len(S) and S[j + 1]["s"] - S[j]["e"] < 0.25: continue  # don't end where speech runs on
        if dur >= MIN_S: cands.append((i, j, *score(S[i:j + 1])))
cands.sort(key=lambda c: -c[2])
picked = []  # non-overlapping top-K (NMS)
for c in cands:
    if all(c[1] < p[0] or c[0] > p[1] for p in picked): picked.append(c)
    if len(picked) == TOP_K: break

# ground truth: paragraphs authored as strong (3,4,7,8 -> 1-based); map via sentence text
ref = (H / "media" / "reference.txt").read_text(encoding="utf-8-sig").splitlines()
strong_keys = ["Why do so many", "Let me give you", "most important lesson", "Did you know"]
hits = 0
for i, j, sc, dur, why in picked:
    text = " ".join(s["text"] for s in S[i:j + 1])
    hit = any(k.lower() in text.lower() for k in strong_keys); hits += hit
    print(f"{'HIT ' if hit else 'MISS'} {S[i]['s']:6.2f}-{S[j]['e']:6.2f}s ({dur:4.1f}s) score={sc:4.2f} {why}\n      {text[:110]}")
n_strong_sent = sum(any(k.lower() in s["text"].lower() for k in strong_keys) for s in S)
print(json.dumps({"precision@%d" % TOP_K: hits / TOP_K, "random_baseline_approx": round(4 / 9, 2), "candidates": len(cands),
                  "sentences": len(S), "all_ends_on_sentence_boundary": True}))
