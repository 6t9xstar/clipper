import { type PointerEvent, type RefObject, useEffect, useRef } from "react";
import { activeGroup, isSpoken, px, textShadow } from "../lib/captions";
import { cropRect, videoBox } from "../lib/framing";
import type { CaptionGroup, CaptionsEdit, FramingEdit, Preset } from "../lib/types";

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
}: Props) {
    const width = (height * 9) / 16;
    const blur = useRef<HTMLCanvasElement>(null);
    const drag = useRef<{ x: number; y: number; fx: number; fy: number } | null>(null);

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
                <div
                    className="pointer-events-none absolute right-0 left-0 flex flex-col items-center text-center"
                    style={{
                        top: `${pos * 100}%`,
                        transform: "translateY(-50%)",
                        padding: `0 ${px(60, height)}px`,
                    }}
                    data-testid="caption"
                >
                    {g.lines.map((line, i) => (
                        <div
                            key={i}
                            style={{
                                fontFamily: pr.font_family,
                                fontWeight: pr.font_weight,
                                fontSize: px(pr.size, height),
                                lineHeight: 1.15,
                                color: pr.color,
                                textShadow: textShadow(pr, height),
                                textTransform: pr.uppercase ? "uppercase" : "none",
                                background: pr.box ? hexa(pr.box) : undefined,
                                padding: pr.box ? `${px(6, height)}px ${px(18, height)}px` : undefined,
                                borderRadius: pr.box ? px(14, height) : undefined,
                            }}
                        >
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
                        </div>
                    ))}
                </div>
            )}
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
