import type { Asset } from "../lib/types";

/** One tile of the asset's thumbnail sprite (layout in media_info.thumbs), scaled to `width`. */
export function Thumb({ asset, t, width = 160 }: { asset: Asset; t: number; width?: number }) {
    const spec = asset.media_info?.thumbs;
    if (!spec || spec.count === 0)
        return <div className="aspect-video rounded bg-neutral-800" style={{ width }} />;
    const idx = Math.min(spec.count - 1, Math.max(0, Math.floor(t / spec.interval_s)));
    const col = idx % spec.cols;
    const row = Math.floor(idx / spec.cols);
    const s = width / spec.tile_w;
    return (
        <div
            aria-hidden
            className="shrink-0 rounded bg-neutral-800 bg-no-repeat"
            style={{
                width,
                height: spec.tile_h * s,
                backgroundImage: `url(/api/v1/assets/${asset.id}/thumbnails)`,
                backgroundSize: `${spec.cols * spec.tile_w * s}px ${spec.rows * spec.tile_h * s}px`,
                backgroundPosition: `${-col * spec.tile_w * s}px ${-row * spec.tile_h * s}px`,
            }}
        />
    );
}
