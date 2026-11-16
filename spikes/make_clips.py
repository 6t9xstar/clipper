"""End-to-end spike: video -> transcript -> scored clip suggestions -> 1080x1920 MP4 + SRT + clips.json.
Usage: uv run python make_clips.py <video> [--top 3] [--min 12] [--max 45]"""
import argparse, json, math, os, re, subprocess, time
from collections import Counter
from pathlib import Path

if os.name == "nt":
    import site
    for sp in site.getsitepackages():
        for b in Path(sp, "nvidia").glob("*/bin"):
            os.add_dll_directory(str(b)); os.environ["PATH"] = f"{b};{os.environ['PATH']}"

ap = argparse.ArgumentParser()
ap.add_argument("video"); ap.add_argument("--top", type=int, default=3)
ap.add_argument("--min", type=float, default=12); ap.add_argument("--max", type=float, default=45)
A = ap.parse_args()
SRC = Path(A.video).resolve()
OUT = Path(__file__).parent / "out" / "clips" / SRC.stem.replace(" ", "_")
OUT.mkdir(parents=True, exist_ok=True)
T0 = time.perf_counter()

# ---------- 1. transcribe ----------
tj = OUT / "transcript.json"
if not tj.exists():
    from faster_whisper import WhisperModel
    try:
        m = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16"); dev = "cuda"
    except Exception as e:  # GPU missing -> CPU fallback
        print("GPU unavailable, using CPU:", e); m = WhisperModel("small", device="cpu", compute_type="int8"); dev = "cpu"
    segs, info = m.transcribe(str(SRC), word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
    words = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)} for s in segs for w in s.words]
    tj.write_text(json.dumps({"device": dev, "language": info.language, "duration": info.duration, "words": words}))
T = json.loads(tj.read_text()); words = T["words"]
t_asr = time.perf_counter() - T0

# ---------- 2. sentences ----------
# Whisper sometimes drops punctuation for long stretches -> also split on pauses, and hard-cap length
sents, cur = [], []
for k, w in enumerate(words):
    cur.append(w)
    gap = words[k + 1]["s"] - w["e"] if k + 1 < len(words) else 9
    if re.search(r"[.?!]$", w["w"]) or gap >= 0.45 or (len(cur) >= 40 and gap >= 0.2):
        sents.append(cur); cur = []
if cur: sents.append(cur)


def split_long(s, max_s=10.0):  # adaptive: cut overlong runs at their largest internal pause, recursively
    if s[-1]["e"] - s[0]["s"] <= max_s or len(s) < 4: return [s]
    k = max(range(1, len(s)), key=lambda q: s[q]["s"] - s[q - 1]["e"])
    return split_long(s[:k], max_s) + split_long(s[k:], max_s)


sents = [p for s in sents for p in split_long(s)]
S =[{"text": " ".join(w["w"] for w in s), "s": s[0]["s"], "e": s[-1]["e"], "words": s} for s in sents]

# ---------- 3. score (heuristics from P4) ----------
STOP = set("the a an and or but so to of in on for is are was were it that this we you i they he she be with at as by do did not have has just".split())
FILLERS = re.compile(r"\b(um+|uh+|(?<!did )you know(?! that)|i mean|what was i going to say|where was i|anyway|maybe)\b", re.I)
DANGLING = re.compile(r"^(and|so|but|then|because|which|also|anyway|right|sure|exactly|okay)\b|^(he|she|they|it|that|this|those)\b", re.I)
HOOK = re.compile(r"\?|^(here is|here's|did you know|the (most|biggest|one)|never|always|why|how|what)\b|\b(secret|mistake|lesson|important|surprising|myth|tip|rule)\b", re.I)
NUM = re.compile(r"\b(\d+|one|two|three|five|six|ten|twenty|hundred|thousand|percent|minutes?|days?|weeks?|months?)\b", re.I)
tf = Counter(t for s in S for t in re.findall(r"[a-z']+", s["text"].lower()) if t not in STOP and len(t) > 3)
WTS = {"topic_break": 2.0, "hook": 2.0, "self_contained": 1.5, "filler": 1.5, "specifics": 1.0, "question_payoff": 1.0,
       "ending": 0.5, "density": 0.5, "salience": 0.5}
WHY = {"hook": "strong opening line", "specifics": "concrete facts/numbers", "question_payoff": "asks a question and answers it",
       "self_contained": "starts mid-thought", "filler": "contains filler", "topic_break": "spans a topic change",
       "ending": "ends on a complete point"}


def score(c):
    first, text = c[0]["text"], " ".join(s["text"] for s in c)
    dur = c[-1]["e"] - c[0]["s"]
    toks = re.findall(r"[a-z']+", text.lower())
    gaps = [c[k + 1]["s"] - c[k]["e"] for k in range(len(c) - 1)]
    f = {"hook": 1.0 if HOOK.search(first) and not FILLERS.search(first) else 0.0,
         "topic_break": -1.0 if any(g > 1.5 for g in gaps) else 0.0,
         "self_contained": -1.0 if DANGLING.search(first) else 0.0,
         "filler": -min(1.0, len(FILLERS.findall(text)) / 2),
         "specifics": min(1.0, len(NUM.findall(text)) / 2),
         "question_payoff": 1.0 if any("?" in s["text"] for s in c[:-1]) else 0.0,
         "ending": 0.0 if c[-1]["text"].endswith("?") else 1.0,
         "density": min(1.0, (len(toks) / dur) / 2.8),
         "salience": min(1.0, sum(math.log1p(tf[t]) for t in toks if t in tf) / max(1, len(toks)) / 0.6)}
    total = sum(WTS[k] * v for k, v in f.items())
    reasons = [WHY[k] for k, v in sorted(f.items(), key=lambda kv: -abs(WTS[kv[0]] * kv[1])) if v and k in WHY][:3]
    return total, dur, reasons


cands = []
for i in range(len(S)):
    for j in range(i, len(S)):
        dur = S[j]["e"] - S[i]["s"]
        if dur > A.max: break
        if dur >= A.min:
            sc, d, why = score(S[i:j + 1]); cands.append((None, i, j, sc, d, why))
cands.sort(key=lambda c: -c[3])
picked = []
for c in cands:
    if all(c[2] < p[1] or c[1] > p[2] for p in picked): picked.append(c)
    if len(picked) == A.top: break
picked.sort(key=lambda c: c[1])

# ---------- 4. render ----------
def ts_ass(t): return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"
def ts_srt(t): return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d},{int(round(t % 1 * 1000)) % 1000:03d}"
def esc(s): return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def caption_groups(ws, max_words=3, max_chars=18):
    g, cur = [], []
    for w in ws:
        if cur and (len(cur) >= max_words or len(" ".join(x["w"] for x in cur + [w])) > max_chars or re.search(r"[.?!,]$", cur[-1]["w"])):
            g.append(cur); cur = []
        cur.append(w)
    return g + ([cur] if cur else [])


COMP = "acompressor=threshold=-20dB:ratio=3:attack=5:release=120:makeup=2"  # tame peaks so -14 LUFS fits under TP
meta = []
for n, (_, i, j, sc, dur, why) in enumerate(picked, 1):
    ws = [w for s in S[i:j + 1] for w in s["words"]]
    pad0, pad1 = 0.15, 0.35
    start, end = max(0, ws[0]["s"] - pad0), ws[-1]["e"] + pad1
    ev, srt = [], []
    nxt = {id(w): (ws[q + 1]["s"] if q + 1 < len(ws) else w["e"] + 0.3) for q, w in enumerate(ws)}
    for k, g in enumerate(caption_groups(ws), 1):
        clean = [re.sub(r"[.,!?;:]+$", "", w["w"]).upper() for w in g]
        for a, w in enumerate(g):  # one event per active word: active word yellow & slightly larger
            # hold until the next word starts (bridges short pauses) but never overlap the next event
            ws_end = min(nxt[id(w)], w["e"] + 0.6)
            txt = " ".join((r"{\c&H00E5FF&\fscx110\fscy110}" + esc(c) + r"{\r}") if b == a else esc(c) for b, c in enumerate(clean))
            ev.append(f"Dialogue: 0,{ts_ass(w['s'] - start)},{ts_ass(ws_end - start)},Cap,,0,0,0,,{txt}")
        srt.append(f"{k}\n{ts_srt(g[0]['s'] - start)} --> {ts_srt(g[-1]['e'] - start)}\n{' '.join(x['w'] for x in g)}\n")
    name = f"clip{n:02d}"
    (OUT / f"{name}.ass").write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,6,3,2,60,60,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev) + "\n", encoding="utf-8")
    (OUT / f"{name}.srt").write_text("\n".join(srt), encoding="utf-8")
    # two-pass EBU R128: measure the exact clip range, then apply linear normalisation
    m1 = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(SRC), "-vn",
                         "-af", COMP + ",loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True).stderr
    L = json.loads(m1[m1.rindex("{"):m1.rindex("}") + 1])
    ln = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={L['input_i']}:measured_TP={L['input_tp']}:measured_LRA={L['input_lra']}:"
          f"measured_thresh={L['input_thresh']}:offset={L['target_offset']}:linear=true")
    fc = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:2,eq=brightness=-0.15[bg];"
          "[b]scale=1080:-2[fg];[bg][fg]overlay=0:(H-h)/2-180,ass=" + f"{name}.ass,format=yuv420p[v];"
          f"[0:a]{COMP},{ln},aresample=48000[a2]")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(SRC),
                        "-filter_complex", fc, "-map", "[v]", "-map", "[a2]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", f"{name}.mp4"],
                       cwd=OUT, capture_output=True, text=True)
    if r.returncode: raise SystemExit(f"render failed for {name}: {r.stderr[-800:]}")
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height",
                                    "-of", "json", str(OUT / f"{name}.mp4")], capture_output=True, text=True).stdout)
    meta.append({"file": f"{name}.mp4", "source_start": round(start, 2), "source_end": round(end, 2),
                 "duration": round(float(pr["format"]["duration"]), 2), "score": round(sc, 2), "reasons": why,
                 "size": [pr["streams"][0]["width"], pr["streams"][0]["height"]],
                 "transcript": " ".join(s["text"] for s in S[i:j + 1])})
(OUT / "clips.json").write_text(json.dumps({"source": SRC.name, "asr_device": T["device"], "candidates": len(cands), "clips": meta}, indent=1))
print(json.dumps({"asr_s": round(t_asr, 1), "total_s": round(time.perf_counter() - T0, 1), "sentences": len(S),
                  "candidates": len(cands), "out": str(OUT)}))
for c in meta: print(f"{c['file']}  {c['source_start']:6.1f}-{c['source_end']:6.1f}s  ({c['duration']}s)  score={c['score']}  {c['reasons']}\n   {c['transcript']}")
