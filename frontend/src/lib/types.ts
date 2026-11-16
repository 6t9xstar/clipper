// Mirrors the backend schemas (backend/clipper/api/schemas.py). Keep in sync.

export interface Page<T> {
    items: T[];
    next_cursor: string | null;
}

export interface Project {
    id: string;
    name: string;
    created_at: number;
    updated_at: number;
}

export type AssetStatus = "uploading" | "processing" | "ready" | "failed";

export interface ThumbSpec {
    count: number;
    interval_s: number;
    cols: number;
    rows: number;
    tile_w: number;
    tile_h: number;
}

export interface Asset {
    id: string;
    project_id: string;
    display_name: string;
    status: AssetStatus;
    size_bytes: number;
    received_bytes: number;
    rights_type: string;
    duration_ms: number | null;
    width: number | null;
    height: number | null;
    fps: number | null;
    has_audio: boolean | null;
    warnings: string[];
    media_info: { thumbs?: ThumbSpec; proxy?: { width: number; height: number } } | null;
    import_job_id: string | null;
    analysis_job_id: string | null;
    error_code: string | null;
    error_message: string | null;
    error_hint: string | null;
    created_at: number;
    upload_url: string | null;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Job {
    id: string;
    type: string;
    status: JobStatus;
    attempts: number;
    max_attempts: number;
    progress: number;
    progress_message: string | null;
    cancel_requested: boolean;
    result: Record<string, unknown> | null;
    error_code: string | null;
}

export type ClipStatus = "suggested" | "approved" | "rejected";

export interface CaptionsEdit {
    enabled: boolean;
    preset: string;
    position: number | null;
}

export interface FramingEdit {
    mode: "crop" | "fit";
    x: number;
    y: number;
    zoom: number;
}

export interface ClipEdit {
    captions: CaptionsEdit;
    framing: FramingEdit;
}

export interface Clip {
    id: string;
    project_id: string;
    asset_id: string;
    start_ms: number;
    end_ms: number;
    origin: "ai" | "manual";
    status: ClipStatus;
    score: number | null;
    rank: number | null;
    reasons: string[];
    features: Record<string, number>;
    title: string | null;
    edit: ClipEdit;
    version: number;
}

export interface Word {
    w: string;
    s: number;
    e: number;
    p?: number;
}

export interface Transcript {
    language: string;
    model: string;
    device: string;
    word_count: number;
    edited: boolean;
    version: number;
    notes: string[];
    words: Word[];
}

export interface Preset {
    id: string;
    name: string;
    description: string;
    font_file: string;
    /** CSS family declared in index.css for the bundled font file */
    font_family: string;
    /** em size in px of the 1080x1920 frame */
    size: number;
    uppercase: boolean;
    color: string;
    highlight: string | null;
    outline: number;
    outline_color: string;
    shadow: number;
    box: string | null;
    box_padding: number;
    /** distance between line centres, in em */
    line_height: number;
    /** height of the box libass draws around a line, in em */
    font_box: number;
    max_words: number;
    max_chars_per_line: number;
    max_lines: number;
    position: number;
}

export interface CaptionGroup {
    s: number;
    e: number;
    lines: Word[][];
    /** per line: scale (<= 1) that keeps it inside the frame; the render applies the same */
    fit: number[];
}

export interface Captions {
    preset: Preset;
    groups: CaptionGroup[];
    note?: string;
}

export const RIGHTS: { value: string; label: string }[] = [
    { value: "own_content", label: "I made this video" },
    { value: "licensed", label: "I have a licence for it" },
    { value: "permission_from_owner", label: "The owner gave me permission" },
    { value: "public_domain_or_cc", label: "Public domain / Creative Commons (licence allows edits)" },
    { value: "other", label: "Other (explain)" },
];

export type ExportStatus = "queued" | "rendering" | "done" | "failed" | "cancelled";

export interface ExportCheck {
    ok: boolean;
    value: unknown;
    [k: string]: unknown;
}

export interface Export {
    id: string;
    project_id: string;
    asset_id: string;
    clip_id: string;
    quality: string;
    status: ExportStatus;
    job_id: string | null;
    clip_version: number;
    title: string | null;
    files: Partial<Record<"mp4" | "srt" | "vtt" | "json", number>>;
    duration_ms: number | null;
    width: number | null;
    height: number | null;
    fps: number | null;
    loudness_lufs: number | null;
    checks: Record<string, ExportCheck>;
    error_code: string | null;
    error_message: string | null;
    created_at: number;
    finished_at: number | null;
    progress: number | null;
    progress_message: string | null;
}

export interface Quality {
    id: string;
    name: string;
    description: string;
    width: number;
    height: number;
}
