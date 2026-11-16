import { useState } from "react";
import type { Word } from "../lib/types";
import { Button, inputCls } from "./ui";

interface Props {
    words: Word[];
    view: [number, number];
    start: number;
    end: number;
    t: number;
    saving: boolean;
    onSeek: (t: number) => void;
    onEdit: (index: number, text: string) => void;
}

/** Words around the clip. Click to jump there; in "Correct words" mode click a word to fix its text (timings
 *  are kept). Clearing a word hides it from captions and scoring. */
export function TranscriptPanel(p: Props) {
    const [editing, setEditing] = useState(false);
    const [open, setOpen] = useState<number | null>(null);
    const [text, setText] = useState("");
    const shown: [Word, number][] = [];
    p.words.forEach((w, i) => {
        if (w.e >= p.view[0] && w.s <= p.view[1]) shown.push([w, i]);
    });

    return (
        <section className="space-y-2">
            <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold">Transcript</h3>
                <Button
                    variant={editing ? "primary" : "ghost"}
                    className="!px-2 !py-0.5 !text-xs"
                    aria-pressed={editing}
                    onClick={() => {
                        setEditing(!editing);
                        setOpen(null);
                    }}
                >
                    {editing ? "Done correcting" : "Correct words"}
                </Button>
                {p.saving && <span className="text-xs text-neutral-500">Saving…</span>}
            </div>
            {shown.length === 0 && <p className="text-xs text-neutral-500">No speech in this part.</p>}
            <p
                className="max-h-56 overflow-y-auto rounded-md bg-neutral-900 p-3 text-sm leading-7"
                data-testid="transcript"
            >
                {shown.map(([w, i]) => {
                    const inside = w.s >= p.start - 0.05 && w.e <= p.end + 0.05;
                    const now = p.t >= w.s && p.t < w.e;
                    if (open === i) {
                        return (
                            <form
                                key={i}
                                className="inline-flex"
                                onSubmit={(e) => {
                                    e.preventDefault();
                                    p.onEdit(i, text);
                                    setOpen(null);
                                }}
                            >
                                <input
                                    autoFocus
                                    aria-label="Word text"
                                    className={`${inputCls} !inline-block !w-32 !py-0`}
                                    value={text}
                                    maxLength={100}
                                    onChange={(e) => setText(e.target.value)}
                                    onBlur={() => setOpen(null)}
                                    onKeyDown={(e) => e.key === "Escape" && setOpen(null)}
                                />
                            </form>
                        );
                    }
                    return (
                        <span key={i}>
                            <button
                                type="button"
                                onClick={() => {
                                    if (editing) {
                                        setOpen(i);
                                        setText(w.w);
                                    } else p.onSeek(w.s);
                                }}
                                className={`rounded px-0.5 ${inside ? "text-neutral-100" : "text-neutral-500"} ${
                                    now ? "bg-violet-600 text-white" : "hover:bg-neutral-700"
                                } ${w.w === "" ? "italic text-neutral-600" : ""}`}
                                title={editing ? "Click to correct" : "Click to jump here"}
                            >
                                {w.w === "" ? "(hidden)" : w.w}
                            </button>{" "}
                        </span>
                    );
                })}
            </p>
        </section>
    );
}
