import { type PointerEvent, type ReactNode, type RefObject, useEffect, useRef, useState } from "react";
import { FRAME_H, activeGroup, isSpoken, px, textShadow } from "../lib/captions";
import { cropRect, videoBox } from "../lib/framing";
import type { CaptionGroup, CaptionsEdit, FramingEdit, OverlayLayout, Preset } from "../lib/types";

interface Props {
    src: string;
    srcW: number;
    srcH: number;
    framing: FramingEdit;
    captions: CaptionsEdit;
    preset?: Preset;
    groups: CaptionGroup[];
    /** clip-relative playback time in seconds */
    t: number;
    height: number;
    videoRef: RefObject<HTMLVideoElement | null>;
    onPan?: (x: number, y: number) => void;
    onTime: (absSeconds: number) => void;
    /** text overlays as laid out by the server, and their styles */
    overlays?: OverlayLayout[];
    overlayStyles?: Preset[];
    selected?: string | null;
    /** an overlay was dragged: its new centre as frame fractions */
    onMoveOverlay?: (id: string, x: number, y: number) => void;
}

/** 9:16 preview of the export: same crop math as the renderer, captions drawn from the same groups. */
export function Preview({
    src,
    srcW,
    srcH,
    framing,
    captions,
    preset: pr,
    groups,
    t,
    height,
    videoRef,
    onPan,
    onTime,
    overlays = [],
    overlayStyles = [],
    selected = null,
    onMoveOverlay,
}: Props) {
    const width = (height * 9) / 16;
    const blur = useRef<HTMLCanvasElement>(null);
    const drag = useRef<{ x: number; y: number; fx: number; fy: number } | null>(null);
    // dragging an overlay moves it locally; the edit (and so the server layout) updates on release
    const [moving, setMoving] = useState<{
        id: string;
        x0: number;
        y0: number;
        dx: number;
        dy: number;
    } | null>(null);
    const ovStart = useRef<{ cx: number; cy: number } | null>(null);

    // per-frame time updates (timeupdate fires only ~4x/s, too coarse for word highlighting)
    useEffect(() => {
        const v = videoRef.current;
        if (!v) return;
        let raf = 0;
        const tick = () => {
            onTime(v.currentTime);
            const c = blur.current;
            if (c && v.readyState >= 2) {
                const ctx = c.getContext("2d");
                if (ctx) {
                    ctx.filter = "blur(10px) brightness(0.6)";
                    const s = Math.max(c.width / v.videoWidth, c.height / v.videoHeight);
                    const w = v.videoWidth * s;
                    const h = v.videoHeight * s;
                    ctx.drawImage(v, (c.width - w) / 2, (c.height - h) / 2, w, h);
                }
            }
            raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
        // rAF is paused for background tabs; media events keep the overlay right after a seek anyway
        const sync = () => onTime(v.currentTime);
        v.addEventListener("seeked", sync);
        v.addEventListener("timeupdate", sync);
        return () => {
            cancelAnimationFrame(raf);
            v.removeEventListener("seeked", sync);
            v.removeEventListener("timeupdate", sync);
        };
    }, [onTime, videoRef]);

    const crop = framing.mode === "crop";
    const rect = cropRect(srcW, srcH, framing.x, framing.y, framing.zoom);
    const box = crop
        ? videoBox(srcW, srcH, rect, height)
        : (() => {
              const s = width / srcW;
              const h = srcH * s;
              return { left: 0, top: (height - h) / 2, width, height: h };
          })();

    const onDown = (e: PointerEvent<HTMLDivElement>) => {
        if (!crop || !onPan) return;
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, y: e.clientY, fx: framing.x, fy: framing.y };
    };
    const onMove = (e: PointerEvent<HTMLDivElement>) => {
        const d = drag.current;
        if (!d || !onPan) return;
        const scale = height / rect.h; // screen px per source px
        const nx = d.fx - (e.clientX - d.x) / scale / srcW;
        const ny = d.fy - (e.clientY - d.y) / scale / srcH;
        onPan(Math.min(1, Math.max(0, nx)), Math.min(1, Math.max(0, ny)));
    };

    const startMove = (e: PointerEvent<HTMLDivElement>, o: OverlayLayout) => {
        if (!onMoveOverlay) return;
        e.stopPropagation(); // not a reframe drag
        e.currentTarget.setPointerCapture(e.pointerId);
        ovStart.current = { cx: o.x, cy: (o.ys[0]! + o.ys[o.ys.length - 1]!) / 2 };
        setMoving({ id: o.id, x0: e.clientX, y0: e.clientY, dx: 0, dy: 0 });
    };
    const dragMove = (e: PointerEvent<HTMLDivElement>) => {
        if (!moving) return;
        e.stopPropagation();
        setMoving({ ...moving, dx: e.clientX - moving.x0, dy: e.clientY - moving.y0 });
    };
    const endMove = (e: PointerEvent<HTMLDivElement>) => {
        if (!moving || !ovStart.current || !onMoveOverlay) return;
        e.stopPropagation();
        const k = FRAME_H / height; // frame px per screen px
        const x = (ovStart.current.cx + (e.clientX - moving.x0) * k) / ((FRAME_H * 9) / 16);
        const y = (ovStart.current.cy + (e.clientY - moving.y0) * k) / FRAME_H;
        onMoveOverlay(moving.id, Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y)));
        setMoving(null);
    };

    const g = captions.enabled ? activeGroup(groups, t) : undefined;
    const pos = captions.position ?? pr?.position ?? 0.7;

    return (
        <div
            className="relative shrink-0 touch-none overflow-hidden rounded-lg bg-black select-none"
            style={{ width, height: height, cursor: crop && onPan ? "grab" : "default" }}
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={() => (drag.current = null)}
            data-testid="preview"
        >
            {!crop && (
                <canvas ref={blur} width={90} height={160} className="absolute inset-0 h-full w-full" />
            )}
            <video
                ref={videoRef}
                src={src}
                preload="auto"
                playsInline
                className="absolute max-w-none"
                style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
            />
            {g && pr && (
                // Same layout as the renderer (backend clipper/render/ass.py): lines are line_height em
                // apart, centred on the caption position, never re-wrapped, scaled by the server's `fit`.
                <div
                    className="pointer-events-none absolute right-0 left-0 flex flex-col items-center text-center"
                    style={{ top: `${pos * 100}%`, transform: "translateY(-50%)" }}
                    data-testid="caption"
                >
                    {g.lines.map((line, i) => (
                        <StyledLine key={i} p={pr} fit={g.fit?.[i] ?? 1} frameH={height}>
                            {line.map((w, k) => (
                                <span
                                    key={k}
                                    style={
                                        pr.highlight && isSpoken(w, t) ? { color: pr.highlight } : undefined
                                    }
                                >
                                    {k > 0 ? " " : ""}
                                    {w.w}
                                </span>
                            ))}
                        </StyledLine>
                    ))}
                </div>
            )}
            {overlays.map((o) => {
                const sp = overlayStyles.find((s) => s.id === o.style);
                if (!sp || t < o.s || t >= o.e) return null;
                const d = moving?.id === o.id ? moving : null;
                return (
                    <div
                        key={o.id}
                        className="pointer-events-none absolute inset-0"
                        style={{ transform: d ? `translate(${d.dx}px, ${d.dy}px)` : undefined }}
                        data-testid="overlay"
                        data-overlay={o.id}
                    >
                        {o.lines.map((text, i) => (
                            // each line centred on the server's (x, y) in frame px, like the ASS pos tag
                            <div
                                key={i}
                                className="absolute"
                                style={{
                                    left: px(o.x, height),
                                    top: px(o.ys[i]!, height),
                                    transform: "translate(-50%, -50%)",
                                    pointerEvents: onMoveOverlay ? "auto" : "none",
                                    cursor: onMoveOverlay ? "move" : undefined,
                                    outline:
                                        selected === o.id ? "1px dashed rgba(167,139,250,0.9)" : undefined,
                                    outlineOffset: 2,
                                }}
                                onPointerDown={(e) => startMove(e, o)}
                                onPointerMove={dragMove}
                                onPointerUp={endMove}
                            >
                                <StyledLine p={sp} fit={o.fit[i] ?? 1} frameH={height}>
                                    {text}
                                </StyledLine>
                            </div>
                        ))}
                    </div>
                );
            })}
        </div>
    );
}

/** One caption/overlay line, styled exactly like the renderer draws it (font, size, outline, box). */
function StyledLine({
    p,
    fit,
    frameH,
    children,
}: {
    p: Preset;
    fit: number;
    frameH: number;
    children: ReactNode;
}) {
    return (
        <div
            className="flex items-center justify-center whitespace-nowrap"
            style={{
                height: px(p.size * p.line_height, frameH),
                fontFamily: p.font_family,
                fontSize: px(p.size, frameH),
                lineHeight: 1,
                color: p.color,
                textShadow: p.box ? undefined : textShadow(p, frameH),
                textTransform: p.uppercase ? "uppercase" : "none",
                transform: fit < 1 ? `scale(${fit})` : undefined,
            }}
        >
            <span
                style={
                    p.box
                        ? {
                              display: "inline-block",
                              lineHeight: p.font_box,
                              padding: px(p.box_padding, frameH),
                              background: hexa(p.box),
                          }
                        : undefined
                }
            >
                {children}
            </span>
        </div>
    );
}

/** #RRGGBBAA -> rgba() (all browsers support 8-digit hex now, but rgba is explicit). */
function hexa(c: string): string {
    const m = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(c);
    if (!m) return c;
    const n = parseInt(m[1]!, 16);
    const a = m[2] ? parseInt(m[2], 16) / 255 : 1;
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a.toFixed(3)})`;
}
