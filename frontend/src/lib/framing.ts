// Exact twin of backend/clipper/edit/framing.py, checked against the same vectors
// (testdata/framing_cases.json), so the preview shows exactly the region the export will contain.

export const OUT_ASPECT = 9 / 16;

export interface Rect {
    x: number;
    y: number;
    w: number;
    h: number;
}

const r4 = (v: number) => Math.round(v * 1e4) / 1e4;

export function cropRect(srcW: number, srcH: number, x = 0.5, y = 0.5, zoom = 1, aspect = OUT_ASPECT): Rect {
    if (srcW <= 0 || srcH <= 0) throw new Error("source size must be positive");
    const z = Math.min(3, Math.max(1, zoom));
    let cw: number;
    let ch: number;
    if (srcW / srcH > aspect) {
        cw = srcH * aspect;
        ch = srcH;
    } else {
        cw = srcW;
        ch = srcW / aspect;
    }
    cw /= z;
    ch /= z;
    const left = Math.min(Math.max(x * srcW - cw / 2, 0), srcW - cw);
    const top = Math.min(Math.max(y * srcH - ch / 2, 0), srcH - ch);
    return { x: r4(left), y: r4(top), w: r4(cw), h: r4(ch) };
}

/** CSS box (relative to a preview frame of height `frameH`) that places the source so the crop fills it. */
export function videoBox(srcW: number, srcH: number, rect: Rect, frameH: number) {
    const s = frameH / rect.h;
    return { left: -rect.x * s, top: -rect.y * s, width: srcW * s, height: srcH * s };
}
