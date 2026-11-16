import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type RefObject, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Thumb } from "../components/Thumb";
import { Badge, Button, ErrorBox, Field, Notice, ProgressBar, inputCls } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { TERMINAL, useJob } from "../lib/queries";
import { fmt } from "../lib/time";
import type { Asset, Clip, ClipStatus, Job, Page } from "../lib/types";

type Filter = "all" | ClipStatus;

export function AssetPage() {
    const { assetId = "" } = useParams();
    const qc = useQueryClient();
    const video = useRef<HTMLVideoElement>(null);
    const stopAt = useRef<number | null>(null);
    const [filter, setFilter] = useState<Filter>("all");
    const asset = useQuery({ queryKey: ["asset", assetId], queryFn: () => api<Asset>(`/assets/${assetId}`) });
    const job = useJob(asset.data?.analysis_job_id);
    const running = !!job.data && !TERMINAL.has(job.data.status);
    const clips = useQuery({
        queryKey: ["clips", assetId],
        queryFn: () => api<Page<Clip>>(`/assets/${assetId}/clips`),
        enabled: asset.data?.status === "ready",
    });

    // refresh the gallery when an analysis finishes
    const lastStatus = useRef<string | undefined>(undefined);
    useEffect(() => {
        const st = job.data?.status;
        if (lastStatus.current && lastStatus.current !== st && st === "succeeded")
            void qc.invalidateQueries({ queryKey: ["clips", assetId] });
        lastStatus.current = st;
    }, [job.data?.status, qc, assetId]);

    const playRange = (c: Clip) => {
        const v = video.current;
        if (!v) return;
        v.currentTime = c.start_ms / 1000;
        stopAt.current = c.end_ms / 1000;
        void v.play();
    };

    if (asset.error) return <ErrorBox error={asset.error} />;
    const a = asset.data;
    if (!a) return <p className="text-sm text-neutral-400">Loading…</p>;
    const items = (clips.data?.items ?? []).filter((c) => filter === "all" || c.status === filter);
    const counts = (s: ClipStatus) => clips.data?.items.filter((c) => c.status === s).length ?? 0;

    return (
        <div className="space-y-6">
            <nav className="text-sm text-neutral-400">
                <Link to="/" className="hover:text-neutral-200">
                    Projects
                </Link>{" "}
                /{" "}
                <Link to={`/projects/${a.project_id}`} className="hover:text-neutral-200">
                    Project
                </Link>{" "}
                / <span className="text-neutral-200">{a.display_name}</span>
            </nav>
            {a.status !== "ready" ? (
                <Notice tone="warn">This video is not ready ({a.status}).</Notice>
            ) : (
                <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
                    <div className="space-y-3">
                        <video
                            ref={video}
                            src={`/api/v1/assets/${a.id}/proxy`}
                            controls
                            preload="metadata"
                            className="aspect-video w-full rounded-lg bg-black"
                            onTimeUpdate={(e) => {
                                if (
                                    stopAt.current !== null &&
                                    e.currentTarget.currentTime >= stopAt.current
                                ) {
                                    e.currentTarget.pause();
                                    stopAt.current = null;
                                }
                            }}
                        />
                        <p className="text-xs text-neutral-500">
                            Preview quality (≤540p). Exports use the original file.
                        </p>
                        <ManualClip asset={a} video={video} />
                    </div>
                    <AnalyzePanel asset={a} job={job.data} running={running} />
                </div>
            )}

            {a.status === "ready" && (
                <section className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 className="mr-2 text-lg font-semibold">Clips</h2>
                        {(["all", "suggested", "approved", "rejected"] as Filter[]).map((f) => (
                            <Button
                                key={f}
                                variant={filter === f ? "primary" : "ghost"}
                                onClick={() => setFilter(f)}
                                aria-pressed={filter === f}
                            >
                                {f === "all"
                                    ? `All (${clips.data?.items.length ?? 0})`
                                    : `${f[0]!.toUpperCase()}${f.slice(1)} (${counts(f)})`}
                            </Button>
                        ))}
                    </div>
                    <ErrorBox error={clips.error} />
                    {clips.data && clips.data.items.length === 0 && (
                        <p className="text-sm text-neutral-400">
                            No clips yet. Run “Find clips”, or add one by hand.
                        </p>
                    )}
                    <ul className="grid gap-3 md:grid-cols-2">
                        {items.map((c) => (
                            <ClipCard key={c.id} clip={c} asset={a} onPlay={() => playRange(c)} />
                        ))}
                    </ul>
                </section>
            )}
        </div>
    );
}

function AnalyzePanel({ asset, job, running }: { asset: Asset; job?: Job; running: boolean }) {
    const qc = useQueryClient();
    const [min, setMin] = useState(15);
    const [max, setMax] = useState(60);
    const [count, setCount] = useState(10);
    const [language, setLanguage] = useState("");
    const [retranscribe, setRetranscribe] = useState(false);
    const start = useMutation({
        mutationFn: (discard: boolean) =>
            api<Job>(`/assets/${asset.id}/analyze`, {
                method: "POST",
                body: {
                    min_seconds: min,
                    max_seconds: max,
                    count,
                    language: language || null,
                    retranscribe,
                    discard_edits: discard,
                },
            }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ["asset", asset.id] }),
    });
    const cancel = useMutation({
        mutationFn: (id: string) => api<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ["job"] }),
    });
    const edited = start.error instanceof ApiError && start.error.code === "TRANSCRIPT_EDITED";
    const notes = (job?.status === "succeeded" && (job.result?.notes as string[] | undefined)) || [];

    return (
        <aside className="space-y-4 rounded-lg border border-neutral-800 p-4">
            <div>
                <h2 className="text-lg font-semibold">Find clips</h2>
                <p className="mt-1 text-xs text-neutral-400">
                    Transcribes the speech on this computer, then suggests self-contained moments with the
                    reasons for each. Suggestions are a starting point — review them.
                </p>
            </div>
            <form
                className="grid grid-cols-3 gap-3"
                onSubmit={(e) => {
                    e.preventDefault();
                    start.mutate(false);
                }}
            >
                <Field label="Min s">
                    <input
                        type="number"
                        className={inputCls}
                        min={3}
                        max={599}
                        value={min}
                        onChange={(e) => setMin(Number(e.target.value))}
                    />
                </Field>
                <Field label="Max s">
                    <input
                        type="number"
                        className={inputCls}
                        min={5}
                        max={600}
                        value={max}
                        onChange={(e) => setMax(Number(e.target.value))}
                    />
                </Field>
                <Field label="How many">
                    <input
                        type="number"
                        className={inputCls}
                        min={1}
                        max={50}
                        value={count}
                        onChange={(e) => setCount(Number(e.target.value))}
                    />
                </Field>
                <div className="col-span-3">
                    <Field
                        label="Spoken language"
                        hint="Detected automatically if empty. Keyword scoring is English-only."
                    >
                        <select
                            className={inputCls}
                            value={language}
                            onChange={(e) => setLanguage(e.target.value)}
                        >
                            <option value="">Detect automatically</option>
                            <option value="en">English</option>
                            <option value="ur">Urdu</option>
                            <option value="hi">Hindi</option>
                            <option value="ar">Arabic</option>
                            <option value="es">Spanish</option>
                            <option value="fr">French</option>
                            <option value="de">German</option>
                            <option value="pt">Portuguese</option>
                        </select>
                    </Field>
                </div>
                <label className="col-span-3 flex items-center gap-2 text-xs text-neutral-300">
                    <input
                        type="checkbox"
                        checked={retranscribe}
                        onChange={(e) => setRetranscribe(e.target.checked)}
                    />
                    Transcribe again (otherwise the saved transcript is reused)
                </label>
                <div className="col-span-3 flex gap-2">
                    <Button
                        type="submit"
                        variant="primary"
                        disabled={running || start.isPending || max <= min}
                    >
                        {asset.analysis_job_id ? "Find clips again" : "Find clips"}
                    </Button>
                    {running && job && (
                        <Button
                            variant="ghost"
                            onClick={() => cancel.mutate(job.id)}
                            disabled={job.cancel_requested}
                        >
                            {job.cancel_requested ? "Cancelling…" : "Cancel"}
                        </Button>
                    )}
                </div>
            </form>
            {max <= min && <p className="text-xs text-red-300">Max must be longer than min.</p>}
            {edited ? (
                <ErrorBox error={start.error}>
                    <Button className="mt-2" variant="danger" onClick={() => start.mutate(true)}>
                        Discard my corrections and transcribe again
                    </Button>
                </ErrorBox>
            ) : (
                <ErrorBox error={start.error ?? cancel.error} />
            )}
            {running && job && (
                <ProgressBar
                    value={job.progress}
                    label={job.status === "queued" ? "Waiting to start" : (job.progress_message ?? "Working")}
                />
            )}
            {job?.status === "failed" && (
                <ErrorBox error={new Error(`Analysis failed (${job.error_code ?? "unknown error"}).`)} />
            )}
            {job?.status === "cancelled" && <Notice>Analysis was cancelled.</Notice>}
            {notes.length > 0 && (
                <Notice>
                    <ul className="list-disc pl-4">
                        {notes.map((n) => (
                            <li key={n}>{n}</li>
                        ))}
                    </ul>
                </Notice>
            )}
        </aside>
    );
}

function ManualClip({ asset, video }: { asset: Asset; video: RefObject<HTMLVideoElement | null> }) {
    const nav = useNavigate();
    const add = useMutation({
        mutationFn: () => {
            const dur = asset.duration_ms ?? 0;
            const start = Math.min(
                Math.round((video.current?.currentTime ?? 0) * 1000),
                Math.max(0, dur - 1000),
            );
            const end = Math.min(dur, start + 30_000);
            return api<Clip>(`/assets/${asset.id}/clips`, {
                method: "POST",
                body: { start_ms: start, end_ms: end },
            });
        },
        onSuccess: (c) => nav(`/clips/${c.id}`),
    });
    return (
        <div>
            <Button onClick={() => add.mutate()} disabled={add.isPending}>
                + Make a clip from the current position
            </Button>
            <ErrorBox error={add.error} />
        </div>
    );
}

const STATUS_TONE: Record<ClipStatus, string> = { suggested: "violet", approved: "green", rejected: "red" };

function ClipCard({ clip: c, asset, onPlay }: { clip: Clip; asset: Asset; onPlay: () => void }) {
    const qc = useQueryClient();
    const refresh = () => qc.invalidateQueries({ queryKey: ["clips", asset.id] });
    const setStatus = useMutation({
        mutationFn: (status: ClipStatus) =>
            api<Clip>(`/clips/${c.id}`, { method: "PATCH", body: { status }, ifMatch: c.version }),
        onSettled: refresh,
    });
    const remove = useMutation({
        mutationFn: () => api<void>(`/clips/${c.id}`, { method: "DELETE" }),
        onSettled: refresh,
    });
    const len = (c.end_ms - c.start_ms) / 1000;
    return (
        <li className="flex gap-3 rounded-lg border border-neutral-800 p-3" data-testid="clip-card">
            <button
                type="button"
                onClick={onPlay}
                aria-label={`Play clip ${c.title ?? ""}`}
                className="self-start"
            >
                <Thumb asset={asset} t={c.start_ms / 1000} width={128} />
            </button>
            <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                    {c.rank != null && (
                        <span className="text-xs font-semibold text-neutral-400">#{c.rank}</span>
                    )}
                    <Badge tone={STATUS_TONE[c.status]}>{c.status}</Badge>
                    {c.origin === "manual" && <Badge>manual</Badge>}
                    <span className="text-xs text-neutral-400">
                        {fmt(c.start_ms / 1000)}–{fmt(c.end_ms / 1000)} · {len.toFixed(1)} s
                        {c.score != null && ` · score ${c.score.toFixed(1)}`}
                    </span>
                </div>
                <p className="truncate text-sm font-medium">{c.title ?? "Untitled clip"}</p>
                {c.reasons.length > 0 && (
                    <ul className="flex flex-wrap gap-1">
                        {c.reasons.map((r) => (
                            <li
                                key={r}
                                className="rounded bg-neutral-800 px-1.5 py-0.5 text-xs text-neutral-300"
                            >
                                {r}
                            </li>
                        ))}
                    </ul>
                )}
                <div className="flex flex-wrap gap-1.5 pt-1">
                    <Link
                        to={`/clips/${c.id}`}
                        className="rounded-md bg-violet-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-violet-500"
                    >
                        Edit
                    </Link>
                    {c.status !== "approved" && (
                        <Button
                            className="!px-2.5 !py-1 !text-xs"
                            onClick={() => setStatus.mutate("approved")}
                        >
                            Approve
                        </Button>
                    )}
                    {c.status !== "rejected" && (
                        <Button
                            className="!px-2.5 !py-1 !text-xs"
                            onClick={() => setStatus.mutate("rejected")}
                        >
                            Reject
                        </Button>
                    )}
                    <Button
                        variant="ghost"
                        className="!px-2.5 !py-1 !text-xs"
                        onClick={() => remove.mutate()}
                    >
                        Delete
                    </Button>
                </div>
                <ErrorBox error={setStatus.error ?? remove.error} />
            </div>
        </li>
    );
}
