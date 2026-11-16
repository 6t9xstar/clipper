"""Render-speed benchmark for M5 exports (docs/benchmarks.md).

Generates its own 1920x1080 30 fps source (moving test pattern + film-grain noise, so it is not
unrealistically easy to encode, and a tone), then times the export steps the `render.export` job runs for a
45 s clip with burned-in captions: loudness measurement, render, verification. No third-party media.

    uv run python scripts/bench_render.py [--seconds 45] [--quality standard]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from clipper.edit import captions as cap
from clipper.edit.schema import ClipEdit
from clipper.render import ass, fontinfo, graph


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=45.0)
    ap.add_argument("--quality", default="standard", choices=sorted(graph.QUALITIES))
    ap.add_argument("--framing", default="crop", choices=["crop", "fit"])
    ap.add_argument(
        "--grain", type=int, default=6, help="noise strength: 6 ~ camera footage, 18 = heavy grain"
    )
    ap.add_argument("--workdir", default="data", help="folder for temporary files (needs ~1 GB free)")
    a = ap.parse_args()
    dur = a.seconds
    Path(a.workdir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=a.workdir) as tmp:
        work = Path(tmp)
        src_path = work / "source.mp4"
        t0 = time.perf_counter()
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={dur + 2}",
             "-f", "lavfi", "-i", f"sine=f=220:d={dur + 2}", "-vf", f"noise=alls={a.grain}:allf=t",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-shortest", str(src_path)],
            check=True,
        )  # fmt: skip
        print(f"source generated in {time.perf_counter() - t0:.1f} s")

        src = graph.Source(src_path, 1920, 1080, 30.0, False, True)
        edit = ClipEdit().model_dump()
        edit["framing"]["mode"] = a.framing
        preset = cap.PRESETS[edit["captions"]["preset"]]
        words = [
            {"w": f"word{i % 9}", "s": 1 + i * 0.35, "e": 1 + i * 0.35 + 0.3} for i in range(int(dur / 0.35))
        ]
        groups = cap.group_words(words, 1.0, 1.0 + dur, preset)
        (work / graph.ASS_NAME).write_text(ass.build(groups, preset, None, dur), encoding="utf-8")
        (work / graph.FONTS_NAME).mkdir()
        shutil.copy2(fontinfo.FONTS_DIR / preset.font_file, work / graph.FONTS_NAME)

        t = time.perf_counter()
        r = subprocess.run(
            graph.loudness_cmd(src, 1.0, dur), capture_output=True, text=True, encoding="utf-8"
        )
        plan = graph.audio_plan(graph.parse_loudnorm(r.stderr))
        t_loud = time.perf_counter() - t

        out = work / "clip.mp4"
        q = graph.QUALITIES[a.quality]
        argv = graph.render_cmd(src, 1.0, dur, edit, q, captions=True, audio=plan, out=out)
        t = time.perf_counter()
        subprocess.run(argv, check=True, cwd=work, capture_output=True)
        t_render = time.perf_counter() - t

        t = time.perf_counter()
        r = subprocess.run(graph.ebur128_cmd(out), capture_output=True, text=True, encoding="utf-8")
        t_verify = time.perf_counter() - t
        total = t_loud + t_render + t_verify
        print(
            f"{dur:.0f} s clip, {q.width}x{q.height} {a.quality}, framing {a.framing}, {len(groups)} captions"
        )
        print(f"  loudness pass {t_loud:6.1f} s")
        print(f"  render        {t_render:6.1f} s   ({dur / t_render:.2f}x realtime)")
        print(f"  verify        {t_verify:6.1f} s")
        print(f"  total         {total:6.1f} s   output {out.stat().st_size / 1e6:.1f} MB, "
              f"{graph.parse_ebur128(r.stderr)} LUFS")  # fmt: skip


if __name__ == "__main__":
    main()
