import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { Badge, Button, ErrorBox, Field, Notice, ProgressBar, inputCls } from "../components/ui";
import { api } from "../lib/api";
import { useJob } from "../lib/queries";
import { bytes, fmt } from "../lib/time";
import { RIGHTS, type Asset, type Page, type Project } from "../lib/types";
import { uploadFile } from "../lib/upload";

const ACCEPT = ".mp4,.mov,.m4v,.mkv,.webm,.avi,.ts,.mts,.m2ts,.flv";

export function ProjectPage() {
    const { projectId = "" } = useParams();
    const project = useQuery({
        queryKey: ["project", projectId],
        queryFn: () => api<Project>(`/projects/${projectId}`),
    });
    const assets = useQuery({
        queryKey: ["assets", projectId],
        queryFn: () => api<Page<Asset>>(`/projects/${projectId}/assets`),
        refetchInterval: (q) => (q.state.data?.items.some((a) => a.status === "processing") ? 1500 : false),
    });

    return (
        <div className="space-y-6">
            <nav className="text-sm text-neutral-400">
                <Link to="/" className="hover:text-neutral-200">
                    Projects
                </Link>{" "}
                / <span className="text-neutral-200">{project.data?.name ?? "…"}</span>
            </nav>
            <ErrorBox error={project.error} />
            <ImportForm projectId={projectId} />
            <section className="space-y-3">
                <h2 className="text-lg font-semibold">Videos</h2>
                <ErrorBox error={assets.error} />
                {assets.data?.items.length === 0 && (
                    <p className="text-sm text-neutral-400">No videos yet.</p>
                )}
                <ul className="space-y-2">
                    {assets.data?.items.map((a) => (
                        <AssetRow key={a.id} asset={a} />
                    ))}
                </ul>
            </section>
        </div>
    );
}

function ImportForm({ projectId }: { projectId: string }) {
    const qc = useQueryClient();
    const fileRef = useRef<HTMLInputElement>(null);
    const [file, setFile] = useState<File | null>(null);
    const [rights, setRights] = useState("own_content");
    const [note, setNote] = useState("");
    const [confirmed, setConfirmed] = useState(false);
    const [progress, setProgress] = useState<number | null>(null);
    const abort = useRef<AbortController | null>(null);

    const start = useMutation({
        mutationFn: async () => {
            if (!file) throw new Error("Choose a video file first.");
            const asset = await api<Asset>(`/projects/${projectId}/assets`, {
                method: "POST",
                body: {
                    filename: file.name,
                    size_bytes: file.size,
                    rights_type: rights,
                    rights_note: note.trim() || null,
                    rights_confirmed: confirmed,
                },
            });
            void qc.invalidateQueries({ queryKey: ["assets", projectId] });
            abort.current = new AbortController();
            setProgress(0);
            return uploadFile(asset, file, {
                signal: abort.current.signal,
                onProgress: (s, t) => setProgress(s / t),
            });
        },
        onSettled: () => {
            setProgress(null);
            void qc.invalidateQueries({ queryKey: ["assets", projectId] });
        },
        onSuccess: () => {
            setFile(null);
            setConfirmed(false);
            setNote("");
            if (fileRef.current) fileRef.current.value = "";
        },
    });

    const needNote = rights === "other" && !note.trim();
    return (
        <section className="rounded-lg border border-neutral-800 p-4">
            <h2 className="text-lg font-semibold">Import a video</h2>
            <p className="mt-1 text-sm text-neutral-400">
                Only import videos you made or have permission to edit. Clipper does not download from
                websites.
            </p>
            <form
                className="mt-4 grid gap-4 md:grid-cols-2"
                onSubmit={(e) => {
                    e.preventDefault();
                    start.mutate();
                }}
            >
                <Field label="Video file" hint="MP4, MOV, MKV, WebM, AVI, TS or FLV">
                    <input
                        ref={fileRef}
                        type="file"
                        accept={ACCEPT}
                        className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-neutral-800 file:px-3 file:py-1.5 file:text-neutral-100"
                        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                        disabled={start.isPending}
                    />
                </Field>
                <Field label="Your right to use it">
                    <select
                        className={inputCls}
                        value={rights}
                        onChange={(e) => setRights(e.target.value)}
                        disabled={start.isPending}
                    >
                        {RIGHTS.map((r) => (
                            <option key={r.value} value={r.value}>
                                {r.label}
                            </option>
                        ))}
                    </select>
                </Field>
                {(rights === "other" || rights === "licensed" || rights === "permission_from_owner") && (
                    <Field label={rights === "other" ? "Explain (required)" : "Note (optional)"}>
                        <input
                            className={inputCls}
                            value={note}
                            maxLength={1000}
                            onChange={(e) => setNote(e.target.value)}
                            disabled={start.isPending}
                            placeholder="e.g. licence name or who gave permission"
                        />
                    </Field>
                )}
                <label className="flex items-start gap-2 text-sm md:col-span-2">
                    <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={confirmed}
                        onChange={(e) => setConfirmed(e.target.checked)}
                        disabled={start.isPending}
                    />
                    <span>I confirm I have the right to use and edit this video.</span>
                </label>
                <div className="flex items-center gap-2 md:col-span-2">
                    <Button
                        type="submit"
                        variant="primary"
                        disabled={!file || !confirmed || needNote || start.isPending}
                    >
                        {start.isPending ? "Uploading…" : "Import"}
                    </Button>
                    {start.isPending && (
                        <Button variant="ghost" onClick={() => abort.current?.abort()}>
                            Pause
                        </Button>
                    )}
                    {file && !start.isPending && (
                        <span className="text-xs text-neutral-400">
                            {file.name} · {bytes(file.size)}
                        </span>
                    )}
                </div>
                {progress !== null && (
                    <div className="md:col-span-2">
                        <ProgressBar value={progress} label="Uploading" />
                    </div>
                )}
            </form>
            {start.error && !(start.error instanceof DOMException) && (
                <div className="mt-3">
                    <ErrorBox error={start.error} />
                </div>
            )}
            {start.error instanceof DOMException && (
                <div className="mt-3">
                    <Notice>Upload paused. Use “Resume” on the video below and choose the same file.</Notice>
                </div>
            )}
        </section>
    );
}

const STATUS_TONE: Record<string, string> = {
    ready: "green",
    failed: "red",
    processing: "violet",
    uploading: "amber",
};

function AssetRow({ asset: a }: { asset: Asset }) {
    const qc = useQueryClient();
    const job = useJob(a.status === "processing" ? a.import_job_id : null);
    const resumeRef = useRef<HTMLInputElement>(null);
    const [resume, setResume] = useState<number | null>(null);
    const invalidate = () => qc.invalidateQueries({ queryKey: ["assets", a.project_id] });
    const remove = useMutation({
        mutationFn: () => api<void>(`/assets/${a.id}`, { method: "DELETE" }),
        onSuccess: invalidate,
    });
    const upload = useMutation({
        mutationFn: (f: File) => uploadFile(a, f, { onProgress: (s, t) => setResume(s / t) }),
        onSettled: () => {
            setResume(null);
            void invalidate();
        },
    });

    return (
        <li className="rounded-lg border border-neutral-800 p-3">
            <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                        {a.status === "ready" ? (
                            <Link
                                to={`/assets/${a.id}`}
                                className="truncate font-medium hover:text-violet-300"
                            >
                                {a.display_name}
                            </Link>
                        ) : (
                            <span className="truncate font-medium">{a.display_name}</span>
                        )}
                        <Badge tone={STATUS_TONE[a.status]}>{a.status}</Badge>
                    </div>
                    <p className="mt-0.5 text-xs text-neutral-400">
                        {bytes(a.size_bytes)}
                        {a.duration_ms != null && ` · ${fmt(a.duration_ms / 1000)}`}
                        {a.width != null && ` · ${a.width}×${a.height}`}
                        {a.has_audio === false && " · no audio"}
                    </p>
                </div>
                {a.status === "ready" && (
                    <Link
                        to={`/assets/${a.id}`}
                        className="rounded-md bg-violet-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-500"
                    >
                        Open
                    </Link>
                )}
                {a.status === "uploading" && (
                    <>
                        <input
                            ref={resumeRef}
                            type="file"
                            accept={ACCEPT}
                            className="hidden"
                            onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])}
                        />
                        <Button onClick={() => resumeRef.current?.click()} disabled={upload.isPending}>
                            Resume
                        </Button>
                    </>
                )}
                <Button
                    variant="ghost"
                    aria-label={`Delete ${a.display_name}`}
                    onClick={() => {
                        if (confirm(`Delete "${a.display_name}" and its clips from this computer?`))
                            remove.mutate();
                    }}
                >
                    Delete
                </Button>
            </div>
            {a.status === "uploading" && resume === null && (
                <p className="mt-2 text-xs text-amber-300">
                    Upload incomplete ({bytes(a.received_bytes)} of {bytes(a.size_bytes)}). Resume and choose
                    the same file.
                </p>
            )}
            {resume !== null && (
                <div className="mt-2">
                    <ProgressBar value={resume} label="Uploading" />
                </div>
            )}
            {a.status === "processing" && job.data && (
                <div className="mt-2">
                    <ProgressBar value={job.data.progress} label={job.data.progress_message ?? "Preparing"} />
                </div>
            )}
            {a.status === "failed" && (
                <div className="mt-2">
                    <ErrorBox error={new Error(a.error_message ?? "Import failed.")}>
                        {a.error_hint && <p className="mt-0.5 text-red-300/80">{a.error_hint}</p>}
                    </ErrorBox>
                </div>
            )}
            {a.warnings.length > 0 && (
                <ul className="mt-2 text-xs text-amber-300">
                    {a.warnings.map((w) => (
                        <li key={w}>⚠ {w}</li>
                    ))}
                </ul>
            )}
            <ErrorBox error={remove.error ?? upload.error} />
        </li>
    );
}
