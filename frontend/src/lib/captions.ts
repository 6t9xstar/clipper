import type { CaptionGroup, Preset, Word } from "./types";

export const FRAME_H = 1920;

/** The caption group visible at clip-relative time `t` (groups never overlap). */
export function activeGroup(groups: CaptionGroup[], t: number): CaptionGroup | undefined {
    let lo = 0;
    let hi = groups.length - 1;
    while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const g = groups[mid]!;
        if (t < g.s) hi = mid - 1;
        else if (t >= g.e) lo = mid + 1;
        else return g;
    }
    return undefined;
}

export function isSpoken(w: Word, t: number): boolean {
    return t >= w.s && t < w.e;
}

/** Preset sizes are pixels in the 1080x1920 output; scale them to a preview frame of height `frameH`.
 *  The preview uses the same font files as the render, and the server converts sizes for libass, so glyphs
 *  match (checked in backend tests/test_render.py); only anti-aliasing and the outline shape differ. */
export function px(v: number, frameH: number): number {
    return (v * frameH) / FRAME_H;
}

/** Outline as a ring of text-shadows (CSS has no outside-only text stroke), plus the optional drop shadow. */
export function textShadow(p: Preset, frameH: number): string | undefined {
    const parts: string[] = [];
    const o = px(p.outline, frameH);
    if (o > 0) {
        for (let a = 0; a < 16; a++) {
            const r = (a / 16) * Math.PI * 2;
            parts.push(
                `${(Math.cos(r) * o).toFixed(2)}px ${(Math.sin(r) * o).toFixed(2)}px 0 ${p.outline_color}`,
            );
        }
    }
    // libass draws a hard (unblurred) copy shifted down, in black at 80 %
    if (p.shadow > 0) parts.push(`0 ${px(p.shadow, frameH)}px 0 rgba(0,0,0,0.8)`);
    return parts.length ? parts.join(", ") : undefined;
}
