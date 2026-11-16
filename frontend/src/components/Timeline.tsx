import { type KeyboardEvent, type PointerEvent, useEffect, useRef } from "react";
import { fmt } from "../lib/time";
import type { Word } from "../lib/types";

export const MIN_LEN = 1;
export const MAX_LEN = 600;
const SNAP = 0.3;

/** Nearest word edge within SNAP seconds (start handles snap to word starts, end handles to word ends). */
export function snapTo(t: number, words: Word[], edge: "s" | "e"): number {
    let best = t;
    let bd = SNAP;
    for (const w of words) {
        const d = Math.abs(w[edge] - t);
        if (d < bd) {
            bd = d;
            best = w[edge];
        }
    }
    return best;
}

/** Applies a handle move, keeping 0 <= start < end <= duration and MIN_LEN <= length <= MAX_LEN. */
export function moveHandle(
    which: "start" | "end",
    t: number,
    start: number,
    end: number,
    duration: number,
): [number, number] {
    if (which === "start") {
        const s = Math.min(Math.max(t, 0, end - MAX_LEN), end - MIN_LEN);
        return [s, end];
    }
    const e = Math.max(Math.min(t, duration, start + MAX_LEN), start + MIN_LEN);
    return [start, e];
}

interface Props {
    view: [number, number];
    duration: number;
    start: number;
    end: number;
    playhead: number;
    peaks: number[];
    rate: number;
    words: Word[];
    snap: boolean;
    onChange: (start: number, end: number) => void;
    onSeek: (t: number) => void;
}

export function Timeline(p: Props) {
    const canvas = useRef<HTMLCanvasElement>(null);
    const wrap = useRef<HTMLDivElement>(null);
    const dragging = useRef<"start" | "end" | null>(null);
    const [vs, ve] = p.view;
    const span = Math.max(0.001, ve - vs);
    const pct = (t: number) => ((t - vs) / span) * 100;

    useEffect(() => {
        const c = canvas.current;
        if (!c) return;
        const w = (c.width = c.clientWidth * devicePixelRatio);
        const h = (c.height = c.clientHeight * devicePixelRatio);
        const ctx = c.getContext("2d");
        if (!ctx) return;
        ctx.clearRect(0, 0, w, h);
        if (!p.peaks.length || !p.rate) return;
        ctx.fillStyle = "#737373";
        for (let x = 0; x < w; x++) {
            const t0 = vs + (x / w) * span;
            const t1 = vs + ((x + 1) / w) * span;
            let m = 0;
            for (let i = Math.floor(t0 * p.rate); i <= Math.floor(t1 * p.rate) && i < p.peaks.length; i++)
                m = Math.max(m, p.peaks[i] ?? 0);
            const bh = Math.max(1, (m / 255) * h * 0.9);
            ctx.fillRect(x, (h - bh) / 2, 1, bh);
        }
    }, [p.peaks, p.rate, vs, span]);

    const timeAt = (clientX: number) => {
        const r = wrap.current!.getBoundingClientRect();
        return vs + ((clientX - r.left) / r.width) * span;
    };

    const apply = (which: "start" | "end", t: number) => {
        const target = p.snap ? snapTo(t, p.words, which === "start" ? "s" : "e") : t;
        const [s, e] = moveHandle(which, Math.min(Math.max(target, vs), ve), p.start, p.end, p.duration);
        p.onChange(s, e);
    };

    const down = (which: "start" | "end") => (e: PointerEvent<HTMLDivElement>) => {
        e.stopPropagation();
        e.currentTarget.setPointerCapture(e.pointerId);
        dragging.current = which;
    };
    const move = (e: PointerEvent<HTMLDivElement>) => {
        if (dragging.current) apply(dragging.current, timeAt(e.clientX));
    };
    const key = (which: "start" | "end") => (e: KeyboardEvent<HTMLDivElement>) => {
        const step = e.shiftKey ? 1 : 0.1;
        const cur = which === "start" ? p.start : p.end;
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            e.preventDefault();
            const [s, en] = moveHandle(
                which,
                cur + (e.key === "ArrowLeft" ? -step : step),
                p.start,
                p.end,
                p.duration,
            );
            p.onChange(s, en);
        }
    };

    const handle = (which: "start" | "end") => {
        const t = which === "start" ? p.start : p.end;
        return (
            <div
                role="slider"
                tabIndex={0}
                aria-label={which === "start" ? "Clip start" : "Clip end"}
                aria-valuemin={0}
                aria-valuemax={p.duration}
                aria-valuenow={Number(t.toFixed(2))}
                aria-valuetext={fmt(t, true)}
                data-testid={`handle-${which}`}
                onPointerDown={down(which)}
                onPointerMove={move}
                onPointerUp={() => (dragging.current = null)}
                onKeyDown={key(which)}
                className="absolute top-0 bottom-0 z-10 w-3 -translate-x-1/2 cursor-ew-resize rounded bg-violet-400 focus-visible:outline-2 focus-visible:outline-white"
                style={{ left: `${pct(t)}%` }}
            />
        );
    };

    return (
        <div className="space-y-1">
            <div
                ref={wrap}
                className="relative h-20 cursor-pointer rounded-md bg-neutral-900"
                onPointerDown={(e) => p.onSeek(timeAt(e.clientX))}
                data-testid="timeline"
            >
                <canvas ref={canvas} className="absolute inset-0 h-full w-full" />
                <div
                    className="absolute top-0 bottom-0 bg-violet-500/20 ring-1 ring-violet-400/60"
                    style={{ left: `${pct(p.start)}%`, width: `${pct(p.end) - pct(p.start)}%` }}
                />
                <div
                    className="pointer-events-none absolute top-0 bottom-0 w-px bg-white"
                    style={{ left: `${pct(p.playhead)}%` }}
                />
                {handle("start")}
                {handle("end")}
            </div>
            <div className="flex justify-between text-xs text-neutral-500">
                <span>{fmt(vs, true)}</span>
                <span>{fmt(ve, true)}</span>
            </div>
        </div>
    );
}
