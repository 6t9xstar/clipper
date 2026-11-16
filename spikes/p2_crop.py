"""P2: render a per-frame 9:16 crop path from 1080p. (a) ffmpeg sendcmd  (b) PyAV decode + numpy crop -> ffmpeg pipe"""
import json, math, subprocess, time
from pathlib import Path

import av

H = Path(__file__).parent
SRC, OUT = H / "media" / "source_1080p.mp4", H / "out"
DUR, FPS, CW = 60.0, 30, 608  # 608x1080 crop -> 1080x1920
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def path_x(i: int) -> int:  # smooth pan: subject moving left<->right, ~10 s period
    return int((1920 - CW) / 2 * (1 + math.sin(2 * math.pi * i / (FPS * 10)))) & ~1


def a_sendcmd():
    cmds = OUT / "crop.cmd"
    cmds.write_text("".join(f"{i / FPS:.4f} crop x {path_x(i)};\n" for i in range(int(DUR * FPS))))
    vf = f"sendcmd=f='{cmds.as_posix().replace(':', chr(92) + ':')}',crop={CW}:1080:0:0,scale=1080:1920:flags=lanczos"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", str(DUR), "-i", str(SRC), "-vf", vf, *ENC, "-an", str(OUT / "p2a.mp4")], check=True)


def b_pyav():
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{CW}x1080", "-r", str(FPS),
                            "-i", "-", "-vf", "scale=1080:1920:flags=lanczos", *ENC, str(OUT / "p2b.mp4")], stdin=subprocess.PIPE)
    with av.open(str(SRC)) as c:
        s = c.streams.video[0]; s.thread_type = "AUTO"
        for i, f in enumerate(c.decode(s)):
            if i >= DUR * FPS: break
            x = path_x(i)
            enc.stdin.write(f.to_ndarray(format="rgb24")[:, x:x + CW].tobytes())
    enc.stdin.close(); enc.wait()


def probe(p):
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,nb_frames", "-of", "json", str(p)],
                                     capture_output=True, text=True).stdout)["streams"][0]


res = {}
for name, fn in (("a_sendcmd", a_sendcmd), ("b_pyav_pipe", b_pyav)):
    t0 = time.perf_counter(); fn(); dt = time.perf_counter() - t0
    res[name] = {"s": round(dt, 1), "x_realtime": round(DUR / dt, 2), **probe(OUT / f"p2{name[0]}.mp4")}
# do both renders agree? (same path -> SSIM should be ~1)
ssim = subprocess.run(["ffmpeg", "-i", str(OUT / "p2a.mp4"), "-i", str(OUT / "p2b.mp4"), "-lavfi", "ssim", "-f", "null", "-"],
                      capture_output=True, text=True).stderr.strip().splitlines()[-1]
print(json.dumps(res), "\n", ssim)
