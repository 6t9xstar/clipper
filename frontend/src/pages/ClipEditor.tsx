import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ExportPanel } from "../components/ExportPanel";
import { Preview } from "../components/Preview";
import { Timeline } from "../components/Timeline";
import { TranscriptPanel } from "../components/TranscriptPanel";
import { Badge, Button, ErrorBox, Field, Notice, inputCls } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { fmt } from "../lib/time";
import type { Asset, Captions, Clip, ClipEdit, ClipStatus, Preset, Transcript } from "../lib/types";

interface Draft {
    start: number;
    end: number;
    title: string;
    edit: ClipEdit;
}

const fromClip = (c: Clip): Draft => ({
    start: c.start_ms / 1000,
    end: c.end_ms / 1000,
    title: c.title ?? "",
    edit: c.edit,
});

function useDebounced<T>(v: T, ms: number): T {
    const [d, setD] = useState(v);
    useEffect(() => {
        const id = setTimeout(() => setD(v), ms);
        return () => clearTimeout(id);
    }, [v, ms]);
    return d;
}

const isTyping = (el: EventTarget | null) =>
    el instanceof HTMLElement &&
    (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

/** Loads the clip and its video, then mounts the editor (keyed by clip id so its state starts fresh). */
export function ClipEditor() {
    const { clipId = "" } = useParams();
    const clipQ = useQuery({ queryKey: ["clip", clipId], queryFn: () => api<Clip>(`/clips/${clipId}`) });
    const assetId = clipQ.data?.asset_id;
    const assetQ = useQuery({
        queryKey: ["asset", assetId],
        queryFn: () => api<Asset>(`/assets/${assetId}`),
        enabled: !!assetId,
    });
    if (clipQ.error || assetQ.error) return <ErrorBox error={clipQ.error ?? assetQ.error} />;
    if (!clipQ.data || !assetQ.data) return <p className="text-sm text-neutral-400">Loading…</p>;
    return <EditorBody key={clipId} clip={clipQ.data} asset={assetQ.data} />;
}

function EditorBody({ clip, asset }: { clip: Clip; asset: Asset }) {
    const clipId = clip.id;
    const qc = useQueryClient();
    const transcriptQ = useQuery({
        queryKey: ["transcript", asset.id],
        queryFn: async () => {
            try {
                return await api<Transcript>(`/assets/${asset.id}/transcript`);
            } catch (e) {
                if (e instanceof ApiError && e.code === "TRANSCRIPT_NOT_FOUND") return null;
                throw e;
            }
        },
    });
    const presetsQ = useQuery({
        queryKey: ["presets"],
        queryFn: () => api<{ items: Preset[] }>("/caption-presets"),
        staleTime: Infinity,
    });
    const waveQ = useQuery({
        queryKey: ["waveform", asset.id],
        queryFn: () => api<{ rate: number; peaks: number[] }>(`/assets/${asset.id}/waveform`),
        staleTime: Infinity,
    });

    const duration = (asset.duration_ms ?? 0) / 1000;
    const [draft, setDraft] = useState<Draft>(() => fromClip(clip));
    const [view, setView] = useState<[number, number]>(() => {
        const pad = Math.max(5, (clip.end_ms - clip.start_ms) / 2000);
        return [Math.max(0, clip.start_ms / 1000 - pad), Math.min(duration, clip.end_ms / 1000 + pad)];
    });
    const [snap, setSnap] = useState(true);
    const [loop, setLoop] = useState(true);
    const [playing, setPlaying] = useState(false);
    const [now, setNow] = useState(clip.start_ms / 1000);
    const video = useRef<HTMLVideoElement>(null);

    const dirty = useMemo(() => JSON.stringify(fromClip(clip)) !== JSON.stringify(draft), [clip, draft]);
    useEffect(() => {
        if (!dirty) return;
        const warn = (e: BeforeUnloadEvent) => e.preventDefault();
        window.addEventListener("beforeunload", warn);
        return () => window.removeEventListener("beforeunload", warn);
    }, [dirty]);

    const capKey = useDebounced(
        draft
            ? {
                  preset: draft.edit.captions.preset,
                  s: Math.round(draft.start * 1000),
                  e: Math.round(draft.end * 1000),
              }
            : null,
        200,
    );
    const captionsQ = useQuery({
        queryKey: ["captions", clipId, capKey, transcriptQ.data?.version],
        queryFn: () =>
            api<Captions>(
                `/clips/${clipId}/captions?preset=${capKey!.preset}&start_ms=${capKey!.s}&end_ms=${capKey!.e}`,
            ),
        enabled: !!capKey,
        placeholderData: keepPreviousData,
    });

    const save = useMutation({
        mutationFn: (d: Draft) =>
            api<Clip>(`/clips/${clipId}`, {
                method: "PATCH",
                ifMatch: clip.version,
                body: {
                    start_ms: Math.round(d.start * 1000),
                    end_ms: Math.round(d.end * 1000),
                    title: d.title.trim() || null,
                    edit: d.edit,
                },
            }),
        onSuccess: (c) => {
            qc.setQueryData(["clip", clipId], c);
            setDraft(fromClip(c));
            void qc.invalidateQueries({ queryKey: ["clips", c.asset_id] });
        },
    });
    const setStatus = useMutation({
        mutationFn: (status: ClipStatus) =>
            api<Clip>(`/clips/${clipId}`, { method: "PATCH", ifMatch: clip.version, body: { status } }),
        onSuccess: (c) => {
            qc.setQueryData(["clip", clipId], c);
            void qc.invalidateQueries({ queryKey: ["clips", c.asset_id] });
        },
    });
    const editWord = useMutation({
        mutationFn: ({ i, w }: { i: number; w: string }) =>
            api<Transcript>(`/assets/${clip.asset_id}/transcript`, {
                method: "PATCH",
                ifMatch: transcriptQ.data?.version,
                body: { words: [{ i, w }] },
            }),
        onSuccess: (t) => qc.setQueryData(["transcript", clip.asset_id], t),
        onError: (e) => {
            if (e instanceof ApiError && e.code === "VERSION_CONFLICT")
                void qc.invalidateQueries({ queryKey: ["transcript", clip.asset_id] });
        },
    });

    // playback limited to the draft range
    const onTime = useCallback(
        (abs: number) => {
            setNow((prev) => (Math.abs(prev - abs) > 1 / 60 ? abs : prev));
            const v = video.current;
            if (!v || v.paused || !draft) return;
            if (abs >= draft.end) {
                if (loop) v.currentTime = draft.start;
                else {
                    v.pause();
                    v.currentTime = draft.end;
                }
            }
        },
        [draft, loop],
    );
    const toggle = useCallback(() => {
        const v = video.current;
        if (!v || !draft) return;
        if (v.paused) {
            if (v.currentTime < draft.start || v.currentTime >= draft.end - 0.05) v.currentTime = draft.start;
            void v.play();
        } else v.pause();
    }, [draft]);
    const seek = (t: number) => {
        if (video.current) video.current.currentTime = t;
        setNow(t);
    };
    useEffect(() => {
        const v = video.current;
        if (!v) return;
        const on = () => setPlaying(!v.paused);
        v.addEventListener("play", on);
        v.addEventListener("pause", on);
        return () => {
            v.removeEventListener("play", on);
            v.removeEventListener("pause", on);
        };
    });
    // seek to the clip start once the video can play
    const seeded = useRef(false);
    useEffect(() => {
        const v = video.current;
        if (!v || !draft || seeded.current) return;
        const go = () => {
            if (!seeded.current) {
                v.currentTime = draft.start;
                seeded.current = true;
            }
        };
        if (v.readyState >= 1) go();
        else v.addEventListener("loadedmetadata", go, { once: true });
    });

    // keyboard: Space play/pause, I/O set start/end at the playhead
    useEffect(() => {
        const k = (e: KeyboardEvent) => {
            if (isTyping(e.target) || e.ctrlKey || e.metaKey || e.altKey || !draft) return;
            const t = video.current?.currentTime ?? now;
            if (e.code === "Space" && !(e.target instanceof HTMLButtonElement)) {
                e.preventDefault();
                toggle();
            } else if (e.key === "i" && t < draft.end - 1) setDraft({ ...draft, start: t });
            else if (e.key === "o" && t > draft.start + 1) setDraft({ ...draft, end: t });
        };
        window.addEventListener("keydown", k);
        return () => window.removeEventListener("keydown", k);
    }, [draft, now, toggle]);

    const presets = presetsQ.data?.items ?? [];
    const preset = presets.find((p) => p.id === draft.edit.captions.preset);
    const words = transcriptQ.data?.words ?? [];
    const setEdit = (fn: (e: ClipEdit) => ClipEdit) => setDraft({ ...draft, edit: fn(draft.edit) });
    const conflict = save.error instanceof ApiError && save.error.code === "VERSION_CONFLICT";
    const len = draft.end - draft.start;

    return (
        <div className="space-y-4">
            <nav className="flex flex-wrap items-center gap-2 text-sm text-neutral-400">
                <Link to={`/assets/${asset.id}`} className="hover:text-neutral-200">
                    ← {asset.display_name}
                </Link>
                <span>/</span>
                <span className="text-neutral-200">{clip.title ?? "Clip"}</span>
                <Badge
                    tone={
                        clip.status === "approved" ? "green" : clip.status === "rejected" ? "red" : "violet"
                    }
                >
                    {clip.status}
                </Badge>
                {clip.reasons.map((r) => (
                    <Badge key={r}>{r}</Badge>
                ))}
            </nav>

            <div className="flex flex-col gap-6 lg:flex-row">
                <div className="flex flex-col items-center gap-3">
                    <Preview
                        src={`/api/v1/assets/${asset.id}/proxy`}
                        srcW={asset.width ?? 16}
                        srcH={asset.height ?? 9}
                        framing={draft.edit.framing}
                        captions={draft.edit.captions}
                        preset={preset}
                        groups={captionsQ.data?.groups ?? []}
                        t={now - draft.start}
                        height={560}
                        videoRef={video}
                        onTime={onTime}
                        onPan={(x, y) => setEdit((e) => ({ ...e, framing: { ...e.framing, x, y } }))}
                    />
                    <div className="flex items-center gap-3 text-sm">
                        <Button variant="primary" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
                            {playing ? "❚❚ Pause" : "▶ Play"}
                        </Button>
                        <span className="tabular-nums text-neutral-300" data-testid="clock">
                            {fmt(Math.max(0, now - draft.start), true)} / {fmt(len, true)}
                        </span>
                        <label className="flex items-center gap-1 text-xs text-neutral-400">
                            <input
                                type="checkbox"
                                checked={loop}
                                onChange={(e) => setLoop(e.target.checked)}
                            />{" "}
                            Loop
                        </label>
                    </div>
                    <p className="max-w-[315px] text-center text-xs text-neutral-500">
                        Same fonts and caption layout as the export (only edge smoothing differs). Drag the
                        picture to reframe.
                    </p>
                </div>

                <div className="min-w-0 flex-1 space-y-5">
                    <div className="flex flex-wrap items-center gap-2">
                        <Button
                            variant="primary"
                            onClick={() => save.mutate(draft)}
                            disabled={!dirty || save.isPending}
                        >
                            {save.isPending ? "Saving…" : dirty ? "Save changes" : "Saved"}
                        </Button>
                        <Button variant="ghost" onClick={() => setDraft(fromClip(clip))} disabled={!dirty}>
                            Undo changes
                        </Button>
                        <span className="mx-2 h-5 w-px bg-neutral-800" />
                        <Button
                            onClick={() => setStatus.mutate("approved")}
                            disabled={clip.status === "approved"}
                        >
                            Approve
                        </Button>
                        <Button
                            onClick={() => setStatus.mutate("rejected")}
                            disabled={clip.status === "rejected"}
                        >
                            Reject
                        </Button>
                    </div>
                    {conflict ? (
                        <ErrorBox error={save.error}>
                            <Button
                                className="mt-2"
                                onClick={async () => {
                                    const fresh = await qc.fetchQuery({
                                        queryKey: ["clip", clipId],
                                        queryFn: () => api<Clip>(`/clips/${clipId}`),
                                    });
                                    setDraft(fromClip(fresh));
                                    save.reset();
                                }}
                            >
                                Load the latest version (discards my unsaved changes)
                            </Button>
                        </ErrorBox>
                    ) : (
                        <ErrorBox error={save.error ?? setStatus.error} />
                    )}

                    <section className="space-y-2">
                        <div className="flex flex-wrap items-center gap-3">
                            <h3 className="text-sm font-semibold">Trim</h3>
                            <span className="text-xs text-neutral-400 tabular-nums" data-testid="range">
                                {fmt(draft.start, true)} → {fmt(draft.end, true)} ({len.toFixed(1)} s)
                            </span>
                            {len > 60 && <Badge tone="amber">longer than 60 s</Badge>}
                            <label className="flex items-center gap-1 text-xs text-neutral-400">
                                <input
                                    type="checkbox"
                                    checked={snap}
                                    onChange={(e) => setSnap(e.target.checked)}
                                />{" "}
                                Snap to words
                            </label>
                            <Button
                                variant="ghost"
                                className="!px-2 !py-0.5 !text-xs"
                                onClick={() => {
                                    const pad = (view[1] - view[0]) / 2;
                                    setView([Math.max(0, view[0] - pad), Math.min(duration, view[1] + pad)]);
                                }}
                            >
                                Zoom out
                            </Button>
                        </div>
                        <Timeline
                            view={view}
                            duration={duration}
                            start={draft.start}
                            end={draft.end}
                            playhead={now}
                            peaks={waveQ.data?.peaks ?? []}
                            rate={waveQ.data?.rate ?? 0}
                            words={words.filter((w) => w.w)}
                            snap={snap}
                            onChange={(start, end) => setDraft({ ...draft, start, end })}
                            onSeek={seek}
                        />
                        <p className="text-xs text-neutral-500">
                            Drag the handles or use ←/→ on a focused handle (Shift = 1 s). Keys: Space play, I
                            = start here, O = end here.
                        </p>
                    </section>

                    {transcriptQ.data ? (
                        <TranscriptPanel
                            words={words}
                            view={view}
                            start={draft.start}
                            end={draft.end}
                            t={now}
                            saving={editWord.isPending}
                            onSeek={seek}
                            onEdit={(i, w) => editWord.mutate({ i, w })}
                        />
                    ) : (
                        <Notice>No transcript yet — run “Find clips” on the video to get captions.</Notice>
                    )}
                    <ErrorBox error={editWord.error} />

                    <section className="space-y-3">
                        <div className="flex items-center gap-3">
                            <h3 className="text-sm font-semibold">Captions</h3>
                            <label className="flex items-center gap-1 text-xs text-neutral-300">
                                <input
                                    type="checkbox"
                                    checked={draft.edit.captions.enabled}
                                    onChange={(e) =>
                                        setEdit((x) => ({
                                            ...x,
                                            captions: { ...x.captions, enabled: e.target.checked },
                                        }))
                                    }
                                />
                                Show captions
                            </label>
                        </div>
                        <div
                            className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4"
                            role="radiogroup"
                            aria-label="Caption style"
                        >
                            {presets.map((p) => (
                                <button
                                    key={p.id}
                                    type="button"
                                    role="radio"
                                    aria-checked={p.id === draft.edit.captions.preset}
                                    onClick={() =>
                                        setEdit((x) => ({ ...x, captions: { ...x.captions, preset: p.id } }))
                                    }
                                    className={`rounded-md border p-2 text-left text-xs ${
                                        p.id === draft.edit.captions.preset
                                            ? "border-violet-500 bg-violet-950/40"
                                            : "border-neutral-800 hover:border-neutral-600"
                                    }`}
                                >
                                    <span className="block text-sm font-semibold">{p.name}</span>
                                    <span className="text-neutral-400">{p.description}</span>
                                </button>
                            ))}
                        </div>
                        <Field
                            label={`Vertical position: ${Math.round((draft.edit.captions.position ?? preset?.position ?? 0.7) * 100)}%`}
                        >
                            <div className="flex items-center gap-2">
                                <input
                                    type="range"
                                    min={0.05}
                                    max={0.95}
                                    step={0.01}
                                    className="flex-1"
                                    value={draft.edit.captions.position ?? preset?.position ?? 0.7}
                                    onChange={(e) =>
                                        setEdit((x) => ({
                                            ...x,
                                            captions: { ...x.captions, position: Number(e.target.value) },
                                        }))
                                    }
                                    aria-label="Caption vertical position"
                                />
                                <Button
                                    variant="ghost"
                                    className="!px-2 !py-0.5 !text-xs"
                                    onClick={() =>
                                        setEdit((x) => ({
                                            ...x,
                                            captions: { ...x.captions, position: null },
                                        }))
                                    }
                                >
                                    Style default
                                </Button>
                            </div>
                        </Field>
                    </section>

                    <section className="space-y-3">
                        <h3 className="text-sm font-semibold">Framing (9:16)</h3>
                        <div className="flex gap-2" role="radiogroup" aria-label="Framing mode">
                            {(
                                [
                                    ["crop", "Fill (crop)"],
                                    ["fit", "Whole frame on blur"],
                                ] as const
                            ).map(([m, label]) => (
                                <Button
                                    key={m}
                                    role="radio"
                                    aria-checked={draft.edit.framing.mode === m}
                                    variant={draft.edit.framing.mode === m ? "primary" : "secondary"}
                                    onClick={() =>
                                        setEdit((x) => ({ ...x, framing: { ...x.framing, mode: m } }))
                                    }
                                >
                                    {label}
                                </Button>
                            ))}
                        </div>
                        {draft.edit.framing.mode === "crop" && (
                            <div className="grid gap-3 sm:grid-cols-3">
                                {(
                                    [
                                        ["x", "Horizontal", 0, 1, 0.005],
                                        ["y", "Vertical", 0, 1, 0.005],
                                        ["zoom", "Zoom", 1, 3, 0.05],
                                    ] as const
                                ).map(([k, label, lo, hi, step]) => (
                                    <Field key={k} label={`${label}: ${draft.edit.framing[k].toFixed(2)}`}>
                                        <input
                                            type="range"
                                            min={lo}
                                            max={hi}
                                            step={step}
                                            className="w-full"
                                            value={draft.edit.framing[k]}
                                            aria-label={`Framing ${label.toLowerCase()}`}
                                            onChange={(e) =>
                                                setEdit((x) => ({
                                                    ...x,
                                                    framing: { ...x.framing, [k]: Number(e.target.value) },
                                                }))
                                            }
                                        />
                                    </Field>
                                ))}
                            </div>
                        )}
                        <p className="text-xs text-neutral-500">
                            Automatic face-following framing is planned for a later milestone; for now the
                            crop stays where you put it.
                        </p>
                    </section>

                    <Field label="Title (for your own reference)">
                        <input
                            className={inputCls}
                            maxLength={200}
                            value={draft.title}
                            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                        />
                    </Field>

                    <ExportPanel clip={clip} dirty={dirty} />
                </div>
            </div>
        </div>
    );
}
