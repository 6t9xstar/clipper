"""P3: crop-path smoothing on simulated face detections (5 fps, noisy, dropouts, one speaker switch).
Compares raw / EMA / One-Euro / One-Euro+deadzone+speed-limit on: jitter, subject-in-frame %, lag."""
import json, math, random

random.seed(7)
FPS, DUR, W, CW = 30, 60, 1920, 608
N = FPS * DUR


def truth(i):  # subject drifts slowly, then a cut to a second speaker at 30 s
    t = i / FPS
    return 700 + 120 * math.sin(t / 3) if t < 30 else 1350 + 60 * math.sin(t / 2)


# detector @5 fps: gaussian noise 25 px, 10% dropouts, 2% false positives
det = {}
for i in range(0, N, FPS // 5):
    r = random.random()
    if r < 0.10: continue
    det[i] = random.uniform(0, W) if r < 0.12 else truth(i) + random.gauss(0, 25)


def held():  # sample-and-hold to 30 fps
    last, out = W / 2, []
    for i in range(N):
        last = det.get(i, last); out.append(last)
    return out


class OneEuro:
    def __init__(s, mincut=0.15, beta=0.004, dcut=1.0):
        s.mc, s.b, s.dc, s.x, s.dx = mincut, beta, dcut, None, 0.0
    def a(s, c): tau = 1 / (2 * math.pi * c); return 1 / (1 + tau * FPS)
    def __call__(s, x):
        if s.x is None: s.x = x; return x
        dx = (x - s.x) * FPS; s.dx += s.a(s.dc) * (dx - s.dx)
        s.x += s.a(s.mc + s.b * abs(s.dx)) * (x - s.x); return s.x


def ema(xs, k=0.08):
    y, out = xs[0], []
    for x in xs: y += k * (x - y); out.append(y)
    return out


def one_euro(xs, **kw):
    f = OneEuro(**kw); return [f(x) for x in xs]


def production(xs, dead=60, vmax=900, cut_jump=350, confirm=3):
    """One-Euro + outlier rejection + dead-zone + speed limit; a jump becomes a hard cut only after
    `confirm` consecutive *detector samples* agree (held frames don't count)."""
    f, cam, out, pend, pend_n = OneEuro(), None, [], None, 0
    for i, x in enumerate(xs):
        if cam is not None and abs(x - cam) > cut_jump:
            if i in det:  # count only fresh detections
                pend_n = pend_n + 1 if pend is not None and abs(x - pend) < cut_jump / 2 else 1; pend = x
            if pend_n < confirm:
                out.append(cam); continue
            f, cam, pend, pend_n = OneEuro(), x, None, 0; f(x); out.append(cam); continue  # hard cut
        pend, pend_n = None, 0
        s = f(x)
        if cam is None: cam = s
        elif abs(s - cam) > dead:
            step = max(-vmax / FPS, min(vmax / FPS, (s - cam) - math.copysign(dead, s - cam)))
            cam += step
        out.append(cam)
    return out


def metrics(path):
    xs = [min(max(c - CW / 2, 0), W - CW) for c in path]  # crop left edge
    jitter = sum(abs(xs[i + 1] - 2 * xs[i] + xs[i - 1]) for i in range(1, N - 1)) / (N - 2)
    inside = sum(xs[i] + 0.15 * CW <= truth(i) <= xs[i] + 0.85 * CW for i in range(N)) / N
    moves = sum(abs(xs[i + 1] - xs[i]) > 0.5 for i in range(N - 1)) / (N - 1)
    return {"jitter_px_per_f2": round(jitter, 2), "subject_in_safe_zone_%": round(100 * inside, 1), "frames_moving_%": round(100 * moves, 1)}


h = held()
print(json.dumps({"raw_hold": metrics(h), "ema": metrics(ema(h)), "one_euro": metrics(one_euro(h)), "production": metrics(production(h))}, indent=1))
