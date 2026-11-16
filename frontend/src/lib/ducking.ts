// Port of backend clipper/edit/voice.py, so the preview ducks the video's sound exactly when the export will.
// duckGain is checked against testdata/ducking_cases.json, the same vectors the backend tests use.

export const ATTACK = 0.12;
export const RELEASE = 0.35;
export const MERGE_GAP = ATTACK + RELEASE + 0.3;

export type Seg = [number, number];

/** Placed takes -> merged clip-time speech segments inside [0, duration]. */
export function placedSegments(placements: { start: number; segments: Seg[] }[], duration: number): Seg[] {
    const segs: Seg[] = [];
    for (const p of placements)
        for (const [a, b] of p.segments)
            if (p.start + b > 0 && p.start + a < duration)
                segs.push([Math.max(0, p.start + a), Math.min(duration, p.start + b)]);
    segs.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const out: Seg[] = [];
    for (const [a, b] of segs) {
        if (b <= a) continue;
        const last = out[out.length - 1];
        if (last && a - last[1] < MERGE_GAP) last[1] = Math.max(last[1], b);
        else out.push([a, b]);
    }
    return out;
}

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

/** Linear gain of the original sound at clip time t (1 = untouched). */
export function duckGain(t: number, segments: Seg[], duckDb: number): number {
    if (duckDb >= 0 || segments.length === 0) return 1;
    const g = 10 ** (duckDb / 20);
    let d = 0;
    for (const [a, b] of segments) {
        const up = clamp01((t - (a - ATTACK)) / ATTACK);
        const down = clamp01((b + RELEASE - t) / RELEASE);
        d = Math.max(d, up * down);
    }
    return 1 - (1 - g) * d;
}

export const dbToGain = (db: number) => 10 ** (db / 20);
