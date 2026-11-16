"""P5: burn word-highlight ASS captions with libass; measure placement (bbox) and per-word highlight timing."""
import json, subprocess
from pathlib import Path

import av
import numpy as np

H = Path(__file__).parent
OUT = H / "out"
W, Hh, FPS = 1080, 1920, 30
FONT, SIZE, MARGIN_V = "Arial", 80, 400
WORDS = [("NEVER", 0.50, 0.90), ("DEPLOY", 0.90, 1.40), ("ON", 1.40, 1.60), ("A", 1.60, 1.75), ("FRIDAY", 1.75, 2.50)]


def ts(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def esc(s):  # ASS text escaping: braces/backslashes would become override tags
    return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


ev = []
for k, (_, s, e) in enumerate(WORDS):  # one event per active word: full line, active word yellow
    txt = " ".join(("{\\c&H00FFFF&}" + esc(w) + "{\\c&HFFFFFF&}") if i == k else esc(w) for i, (w, _, _) in enumerate(WORDS))
    ev.append(f"Dialogue: 0,{ts(s)},{ts(e)},Cap,,0,0,0,,{txt}")
ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {Hh}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},{SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,2,40,40,{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev) + "\n"
(OUT / "cap.ass").write_text(ass, encoding="utf-8")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={W}x{Hh}:r={FPS}:d=3",
                "-vf", "ass=cap.ass", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", "cap.mp4"], check=True, cwd=OUT)

boxes, timing = [], []
with av.open(str(OUT / "cap.mp4")) as c:
    for i, f in enumerate(c.decode(video=0)):
        a = f.to_ndarray(format="rgb24").astype(int)
        lit = a.max(axis=2) > 128
        yellow = (a[..., 0] > 160) & (a[..., 1] > 160) & (a[..., 2] < 90)
        if lit.any():
            ys, xs = np.nonzero(lit); boxes.append((xs.min(), ys.min(), xs.max(), ys.max()))
        if yellow.any():  # which word is yellow? match by x-centre ordering
            timing.append((i / FPS, int(np.nonzero(yellow)[1].mean())))
x0, y0, x1, y1 = boxes[len(boxes) // 2]
first_seen = {}
# map each frame's yellow centroid to word index by clustering centroids into 5 groups in x order
cents = sorted({round(x / 20) for _, x in timing})
for t, x in timing:
    k = cents.index(round(x / 20)) if round(x / 20) in cents else None
    if k is not None and k not in first_seen: first_seen[k] = t
errs = [round(abs(first_seen.get(k, -9) - WORDS[k][1]) * 1000) for k in range(len(WORDS))]
print(json.dumps({"bbox": [int(v) for v in (x0, y0, x1, y1)], "centre_x_err_px": int(abs((x0 + x1) / 2 - W / 2)),
                  "baseline_from_bottom_px": int(Hh - y1), "requested_margin_v": MARGIN_V, "cap_height_px": int(y1 - y0),
                  "highlight_groups": len(cents), "word_onset_err_ms": errs, "frame_ms": round(1000 / FPS, 1)}))
