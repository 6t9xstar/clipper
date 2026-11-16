import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { bytes, fmt } from "../lib/time";
import type { Clip, Export, Page, Quality } from "../lib/types";
import { Badge, Button, ErrorBox, Notice, ProgressBar } from "./ui";

const ACTIVE = new Set(["queued", "rendering"]);
const TONE: Record<string, string> = {
    done: "green",
    failed: "red",
    cancelled: "neutral",
    queued: "amber",
    rendering: "violet",
};

export const fileUrl = (e: Export, kind: string, download = false) =>
    `/api/v1/exports/${e.id}/files/${kind}${download ? "?download=1" : ""}`;

/** Renders the saved clip to a vertical MP4 (plus captions files) and lists this clip's exports. */
export function ExportPanel({ clip, dirty }: { clip: Clip; dirty: boolean }) {
    const qc = useQueryClient();
    const [quality, setQuality] = useState("standard");
    const qualities = useQuery({
        queryKey: ["export-qualities"],
        queryFn: () => api<{ items: Quality[] }>("/export-qualities"),
        staleTime: Infinity,
    });
    const exports = useQuery({
        queryKey: ["exports", clip.id],
        queryFn: () => api<Page<Export>>(`/clips/${clip.id}/exports`),
        refetchInterval: (q) => (q.state.data?.items.some((e) => ACTIVE.has(e.status)) ? 1000 : false),
    });
    const refresh = () => qc.invalidateQueries({ queryKey: ["exports", clip.id] });
    const start = useMutation({
        mutationFn: () =>
            api<Export>(`/clips/${clip.id}/exports`, {
                method: "POST",
                body: { quality },
                ifMatch: clip.version,
            }),
        onSuccess: refresh,
    });

    return (
        <section className="space-y-3 rounded-lg border border-neutral-800 p-3" aria-label="Export">
            <h3 className="text-sm font-semibold">Export</h3>
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Export quality">
                {qualities.data?.items.map((q) => (
                    <button
                        key={q.id}
                        type="button"
                        role="radio"
                        aria-checked={quality === q.id}
                        title={q.description}
                        onClick={() => setQuality(q.id)}
                        className={`rounded-md border px-2.5 py-1 text-xs ${
                            quality === q.id
                                ? "border-violet-500 bg-violet-950/40"
                                : "border-neutral-800 hover:border-neutral-600"
                        }`}
                    >
                        {q.name}
                    </button>
                ))}
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <Button variant="primary" onClick={() => start.mutate()} disabled={dirty || start.isPending}>
                    Export video
                </Button>
                <span className="text-xs text-neutral-400">
                    {dirty
                        ? "Save your changes first — the export uses the saved clip."
                        : "MP4 (H.264/AAC, 9:16) with captions burned in, plus SRT/VTT caption files."}
                </span>
            </div>
            <ErrorBox error={start.error} />
            <ErrorBox error={exports.error} />
            {exports.data && exports.data.items.length > 0 && (
                <ul className="space-y-2" data-testid="exports">
                    {exports.data.items.map((e) => (
                        <ExportRow
                            key={e.id}
                            e={e}
                            current={e.clip_version === clip.version}
                            onChange={refresh}
                        />
                    ))}
                </ul>
            )}
        </section>
    );
}

function ExportRow({ e, current, onChange }: { e: Export; current: boolean; onChange: () => void }) {
    const [watch, setWatch] = useState(false);
    const cancel = useMutation({
        mutationFn: () => api(`/jobs/${e.job_id}/cancel`, { method: "POST" }),
        onSettled: onChange,
    });
    const remove = useMutation({
        mutationFn: () => api<void>(`/exports/${e.id}`, { method: "DELETE" }),
        onSettled: onChange,
    });
    const active = ACTIVE.has(e.status);
    const loud = e.checks.loudness;
    return (
        <li className="space-y-2 rounded-md bg-neutral-900 p-2 text-xs" data-testid="export-row">
            <div className="flex flex-wrap items-center gap-2">
                <Badge tone={TONE[e.status]}>{e.status}</Badge>
                <span className="text-neutral-300">{e.quality}</span>
                <span className="text-neutral-500">{new Date(e.created_at * 1000).toLocaleString()}</span>
                {!current && (
                    <span className="text-amber-300">made from an earlier version of this clip</span>
                )}
                <span className="flex-1" />
                {active && e.job_id && (
                    <Button variant="ghost" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
                        Cancel
                    </Button>
                )}
                {!active && (
                    <Button
                        variant="ghost"
                        aria-label="Delete export"
                        onClick={() => {
                            if (confirm("Delete this exported video and its files from this computer?"))
                                remove.mutate();
                        }}
                    >
                        Delete
                    </Button>
                )}
            </div>
            {active && <ProgressBar value={e.progress ?? 0} label={e.progress_message ?? "Waiting"} />}
            {e.status === "failed" && (
                <ErrorBox error={new Error(e.error_message ?? "The export failed.")}>
                    {e.error_code && <p className="mt-0.5 text-red-300/80">Code: {e.error_code}</p>}
                </ErrorBox>
            )}
            {e.status === "done" && (
                <>
                    <p className="text-neutral-300" data-testid="export-facts">
                        ✓ Checked: {e.width}×{e.height} · {fmt((e.duration_ms ?? 0) / 1000, true)} · {e.fps}{" "}
                        fps
                        {loud?.value != null && ` · ${Number(loud.value).toFixed(1)} LUFS`}
                        {e.files.mp4 != null && ` · ${bytes(e.files.mp4)}`}
                    </p>
                    {loud && !loud.ok && typeof loud.note === "string" && (
                        <p className="text-amber-300" data-testid="export-warning">
                            ⚠ {loud.note}
                        </p>
                    )}
                    <div className="flex flex-wrap gap-2">
                        <a
                            className="rounded-md bg-violet-600 px-2.5 py-1 font-medium text-white hover:bg-violet-500"
                            href={fileUrl(e, "mp4", true)}
                            download
                        >
                            Download MP4
                        </a>
                        {e.files.srt != null && (
                            <a
                                className="rounded-md bg-neutral-800 px-2.5 py-1"
                                href={fileUrl(e, "srt", true)}
                                download
                            >
                                SRT
                            </a>
                        )}
                        {e.files.vtt != null && (
                            <a
                                className="rounded-md bg-neutral-800 px-2.5 py-1"
                                href={fileUrl(e, "vtt", true)}
                                download
                            >
                                VTT
                            </a>
                        )}
                        <a
                            className="rounded-md bg-neutral-800 px-2.5 py-1"
                            href={fileUrl(e, "json")}
                            target="_blank"
                            rel="noreferrer"
                        >
                            Details
                        </a>
                        <Button variant="ghost" onClick={() => setWatch(!watch)}>
                            {watch ? "Hide" : "Watch"}
                        </Button>
                    </div>
                    {watch && (
                        <video
                            src={fileUrl(e, "mp4")}
                            controls
                            playsInline
                            className="max-h-[480px] rounded bg-black"
                            data-testid="export-video"
                        />
                    )}
                    <Notice>
                        Before you post: check the video yourself, and only publish clips you have the rights
                        to.
                    </Notice>
                </>
            )}
            <ErrorBox error={cancel.error ?? remove.error} />
        </li>
    );
}
