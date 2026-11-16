"""Assemble demo voice lines into a 16:9 1080p 'podcast' video: gradient bg, speaker name card, waveform."""
import subprocess
from pathlib import Path

D = Path(__file__).parent / "media" / "demo"
rows = [l.split("\t") for l in (D / "lines.tsv").read_text(encoding="utf-8-sig").splitlines() if l.strip()]
GAP = {"D": 0.35, "Z": 0.35}
parts, t, turns = [], 0.0, []
for f, who, _ in rows:
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f],
                               capture_output=True, text=True).stdout)
    turns.append((t, t + dur, who)); t += dur + GAP[who]
    parts.append(f"file '{Path(f).as_posix()}'\nfile '{(D / 'gap.wav').as_posix()}'")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "0.35", str(D / "gap.wav")], check=True)
(D / "concat.txt").write_text("\n".join(parts))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(D / "concat.txt"), str(D / "audio.wav")], check=True)

font = "C\\:/Windows/Fonts/arialbd.ttf"
name = {"D": "DAVID", "Z": "ZIRA"}
col = {"D": "0x4FC3F7", "Z": "0xFFB74D"}
cards = ",".join(
    f"drawbox=x={'200' if w == 'D' else '1220'}:y=330:w=500:h=500:color={col[w]}@0.9:t=fill:enable='between(t,{s:.2f},{e:.2f})',"
    f"drawtext=fontfile='{font}':text='{name[w]}':fontsize=90:fontcolor=white:x={'450' if w == 'D' else '1470'}-text_w/2:y=540:"
    f"enable='between(t,{s:.2f},{e:.2f})'"
    for s, e, w in turns)
fc = (f"gradients=s=1920x1080:r=30:c0=0x1a1a2e:c1=0x16213e:speed=0.01,"
      f"drawtext=fontfile='{font}':text='SMALL WINS  |  Episode 12':fontsize=56:fontcolor=white@0.85:x=(w-text_w)/2:y=110,"
      f"drawbox=x=200:y=330:w=500:h=500:color=white@0.12:t=fill,drawbox=x=1220:y=330:w=500:h=500:color=white@0.12:t=fill,"
      f"{cards}[bg];"
      f"[0:a]showwaves=s=1520x160:mode=cline:colors=white@0.7:r=30[wv];[bg][wv]overlay=200:880[v]")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(D / "audio.wav"), "-filter_complex", fc, "-map", "[v]", "-map", "0:a",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                "-shortest", str(D.parent / "demo_podcast.mp4")], check=True)
print(f"duration {t:.1f}s, {len(turns)} turns")
