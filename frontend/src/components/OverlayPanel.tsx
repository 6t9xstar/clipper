import type { OverlayEdit, OverlayStyleId, Preset } from "../lib/types";
import { Button, inputCls } from "./ui";

export const MAX_OVERLAYS = 20;
const DEFAULT_LEN = 3; // s

/** Overlays the server accepts (empty text or too-short ones are left out of the layout request). */
export const valid = (o: OverlayEdit) => o.text.trim().length > 0 && o.end - o.start >= 0.2 && o.start >= 0;

const newId = () => Math.random().toString(36).slice(2, 10);

interface Props {
    overlays: OverlayEdit[];
    styles: Preset[];
    /** clip length and the playhead, both in seconds from the clip start */
    length: number;
    t: number;
    selected: string | null;
    onSelect: (id: string | null) => void;
    onChange: (overlays: OverlayEdit[]) => void;
    onSeek: (clipSeconds: number) => void;
}

/** Text overlays (chapter titles, callouts, labels): added at the playhead, dragged in the preview. */
export function OverlayPanel({ overlays, styles, length, t, selected, onSelect, onChange, onSeek }: Props) {
    const update = (id: string, patch: Partial<OverlayEdit>) =>
        onChange(overlays.map((o) => (o.id === id ? { ...o, ...patch } : o)));
    const add = (style: Preset) => {
        const start = Math.max(0, Math.min(t, length - 0.5));
        const o: OverlayEdit = {
            id: newId(),
            style: style.id as OverlayStyleId,
            text: style.id === "title" ? "Chapter title" : style.id === "callout" ? "Look at this" : "Label",
            start: round(start),
            end: round(Math.min(length, start + DEFAULT_LEN)),
            x: 0.5,
            y: style.position,
        };
        onChange([...overlays, o]);
        onSelect(o.id);
    };

    return (
        <section className="space-y-3" aria-label="Text overlays">
            <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold">Text overlays</h3>
                {styles.map((s) => (
                    <Button
                        key={s.id}
                        variant="ghost"
                        className="!px-2 !py-0.5 !text-xs"
                        title={s.description}
                        disabled={overlays.length >= MAX_OVERLAYS}
                        onClick={() => add(s)}
                    >
                        + {s.name}
                    </Button>
                ))}
            </div>
            {overlays.length === 0 ? (
                <p className="text-xs text-neutral-500">
                    Add a chapter title, a callout or a label at the playhead. Added context like this is part
                    of what makes a clip your own work.
                </p>
            ) : (
                <ul className="space-y-2">
                    {overlays.map((o, n) => (
                        <li
                            key={o.id}
                            className={`space-y-2 rounded-md border p-2 text-xs ${
                                selected === o.id ? "border-violet-500" : "border-neutral-800"
                            }`}
                            onFocusCapture={() => onSelect(o.id)}
                            data-testid="overlay-item"
                        >
                            <div className="flex flex-wrap items-center gap-2">
                                <select
                                    className={`${inputCls} !w-auto !py-0.5 text-xs`}
                                    value={o.style}
                                    aria-label={`Overlay ${n + 1} style`}
                                    onChange={(e) =>
                                        update(o.id, { style: e.target.value as OverlayStyleId })
                                    }
                                >
                                    {styles.map((s) => (
                                        <option key={s.id} value={s.id}>
                                            {s.name}
                                        </option>
                                    ))}
                                </select>
                                <TimeInput
                                    label={`Overlay ${n + 1} start`}
                                    value={o.start}
                                    max={length}
                                    onChange={(v) => update(o.id, { start: v })}
                                />
                                <span className="text-neutral-500">→</span>
                                <TimeInput
                                    label={`Overlay ${n + 1} end`}
                                    value={o.end}
                                    max={length}
                                    onChange={(v) => update(o.id, { end: v })}
                                />
                                <Button
                                    variant="ghost"
                                    className="!px-1.5 !py-0.5 !text-xs"
                                    title="Start at the playhead"
                                    onClick={() => update(o.id, { start: round(Math.max(0, t)) })}
                                >
                                    ⇤ here
                                </Button>
                                <Button
                                    variant="ghost"
                                    className="!px-1.5 !py-0.5 !text-xs"
                                    title="End at the playhead"
                                    onClick={() => update(o.id, { end: round(Math.min(length, t)) })}
                                >
                                    here ⇥
                                </Button>
                                <Button
                                    variant="ghost"
                                    className="!px-1.5 !py-0.5 !text-xs"
                                    onClick={() => {
                                        onSelect(o.id);
                                        onSeek(o.start);
                                    }}
                                >
                                    Show
                                </Button>
                                <span className="flex-1" />
                                <Button
                                    variant="ghost"
                                    className="!px-1.5 !py-0.5 !text-xs"
                                    aria-label={`Delete overlay ${n + 1}`}
                                    onClick={() => onChange(overlays.filter((x) => x.id !== o.id))}
                                >
                                    Delete
                                </Button>
                            </div>
                            <textarea
                                className={`${inputCls} text-sm`}
                                rows={2}
                                maxLength={120}
                                value={o.text}
                                aria-label={`Overlay ${n + 1} text`}
                                onChange={(e) => update(o.id, { text: e.target.value })}
                            />
                            {!valid(o) && (
                                <p className="text-amber-300">
                                    {o.text.trim()
                                        ? "The end must be at least 0.2 s after the start."
                                        : "Type some text (or delete this overlay)."}
                                </p>
                            )}
                            {valid(o) && o.start >= length && (
                                <p className="text-amber-300">
                                    Starts after the clip ends, so it is not shown.
                                </p>
                            )}
                        </li>
                    ))}
                </ul>
            )}
            {overlays.length > 0 && (
                <p className="text-xs text-neutral-500">
                    Drag an overlay in the preview to move it. Times are seconds from the clip start; overlays
                    are kept inside the frame edges automatically.
                </p>
            )}
        </section>
    );
}

function TimeInput({
    label,
    value,
    max,
    onChange,
}: {
    label: string;
    value: number;
    max: number;
    onChange: (v: number) => void;
}) {
    return (
        <input
            type="number"
            className={`${inputCls} !w-20 !py-0.5 text-xs tabular-nums`}
            aria-label={label}
            min={0}
            max={Math.max(0, max)}
            step={0.1}
            value={value}
            onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v)) onChange(round(Math.min(600, Math.max(0, v))));
            }}
        />
    );
}

const round = (v: number) => Math.round(v * 10) / 10;
