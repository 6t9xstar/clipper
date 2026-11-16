import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { type Seg, dbToGain, duckGain } from "../lib/ducking";
import { fmt } from "../lib/time";
import type { AudioEdit, VoiceTrack, VoiceoverEdit } from "../lib/types";
import { Button, ErrorBox, Field } from "./ui";

const MIMES = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"];
const DUCK = [
    [0, "Off"],
    [-6, "Light (−6 dB)"],
    [-12, "Medium (−12 dB)"],
    [-18, "Strong (−18 dB)"],
    [-24, "Very strong (−24 dB)"],
] as const;

interface Props {
    clipId: string;
    takes: VoiceTrack[];
    voiceovers: VoiceoverEdit[];
    audio: AudioEdit;
    /** takes used by the saved clip (they cannot be deleted until removed and saved) */
    savedIds: Set<string>;
    segments: Seg[];
    length: number;
    /** playhead, seconds from the clip start */
    t: number;
    onChange: (voiceovers: VoiceoverEdit[], audio: AudioEdit) => void;
    onTakesChanged: () => void;
    /** start the clip playing at clip time t (muted or not) for recording; `onPaused` runs once when the
     *  playback stops (the clip end, or the user pausing), which ends the recording */
    onPlayFrom: (t: number, muted: boolean, onPaused: () => void) => void;
    onStopPlayback: () => void;
    onRecording: (on: boolean) => void;
}

/** Your own commentary: record over the clip (or upload a file), place takes, set levels and ducking. */
export function VoicePanel(p: Props) {
    const { takes, voiceovers, audio, length, t } = p;
    const [rec, setRec] = useState<{ t0: number } | null>(null);
    const recorder = useRef<MediaRecorder | null>(null);
    const [muteVideo, setMuteVideo] = useState(true);
    const [recError, setRecError] = useState<Error | null>(null);
    const fileInput = useRef<HTMLInputElement>(null);

    const upload = useMutation({
        mutationFn: ({ blob, name }: { blob: Blob; name: string; start: number }) =>
            api<VoiceTrack>(`/clips/${p.clipId}/voice?name=${encodeURIComponent(name)}`, {
                method: "POST",
                raw: blob,
            }),
        onSuccess: (track, { start }) => {
            p.onTakesChanged();
            p.onChange([...voiceovers, { track_id: track.id, start: round(start) }], audio);
        },
    });
    const remove = useMutation({
        mutationFn: (id: string) => api<void>(`/voice/${id}`, { method: "DELETE" }),
        onSettled: () => p.onTakesChanged(),
    });

    const start = async () => {
        setRecError(null);
        if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
            setRecError(new Error("This browser cannot record audio. Upload a recording instead."));
            return;
        }
        let stream: MediaStream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
            });
        } catch {
            setRecError(new Error("Microphone access was refused. Allow it in the browser's address bar."));
            return;
        }
        const mime = MIMES.find((m) => MediaRecorder.isTypeSupported(m));
        const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        const chunks: Blob[] = [];
        const t0 = Math.max(0, Math.min(t, length - 0.5));
        mr.ondataavailable = (e) => {
            if (e.data.size) chunks.push(e.data);
        };
        mr.onstop = () => {
            stream.getTracks().forEach((x) => x.stop());
            const blob = new Blob(chunks, { type: mr.mimeType || mime || "audio/webm" });
            upload.mutate({ blob, name: `Take ${takes.length + 1}`, start: t0 });
        };
        recorder.current = mr;
        mr.start(250);
        setRec({ t0 });
        p.onRecording(true);
        p.onPlayFrom(t0, muteVideo, stop); // the clip end pauses the video, which stops the recording
    };
    function stop() {
        const mr = recorder.current;
        if (!mr) return;
        recorder.current = null;
        if (mr.state !== "inactive") mr.stop();
        p.onStopPlayback();
        p.onRecording(false);
        setRec(null);
    }
    const elapsed = rec ? Math.max(0, t - rec.t0) : 0;

    const used = new Set([...voiceovers.map((v) => v.track_id), ...p.savedIds]);
    const setAudio = (patch: Partial<AudioEdit>) => p.onChange(voiceovers, { ...audio, ...patch });
    const setPlacements = (v: VoiceoverEdit[]) => p.onChange(v, audio);
    const nameOf = (id: string) => takes.find((x) => x.id === id)?.name ?? "Missing take";

    return (
        <section className="space-y-3" aria-label="Voiceover">
            <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold">Voiceover</h3>
                {rec ? (
                    <Button variant="danger" onClick={stop}>
                        ■ Stop ({elapsed.toFixed(1)} s)
                    </Button>
                ) : (
                    <Button
                        onClick={() => void start()}
                        disabled={upload.isPending || voiceovers.length >= 10}
                    >
                        ● Record from the playhead
                    </Button>
                )}
                <Button
                    variant="ghost"
                    className="!text-xs"
                    disabled={!!rec || upload.isPending}
                    onClick={() => fileInput.current?.click()}
                >
                    Upload a recording…
                </Button>
                <input
                    ref={fileInput}
                    type="file"
                    accept="audio/*,.webm,.ogg,.m4a,.mp3,.wav,.flac"
                    className="hidden"
                    aria-label="Voice recording file"
                    onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) upload.mutate({ blob: f, name: f.name.slice(0, 100), start: Math.max(0, t) });
                        e.target.value = "";
                    }}
                />
                <label className="flex items-center gap-1 text-xs text-neutral-400">
                    <input
                        type="checkbox"
                        checked={muteVideo}
                        disabled={!!rec}
                        onChange={(e) => setMuteVideo(e.target.checked)}
                    />
                    Mute the video while recording (use headphones to hear it)
                </label>
                {upload.isPending && <span className="text-xs text-neutral-400">Saving the recording…</span>}
            </div>
            <ErrorBox error={recError ?? upload.error ?? remove.error} />

            {voiceovers.length === 0 && !rec && (
                <p className="text-xs text-neutral-500">
                    Talk over the clip: explain, react, add context. Your own commentary is the most important
                    way to make a clip your own work. The video&apos;s sound is turned down while you speak.
                </p>
            )}

            {voiceovers.length > 0 && (
                <>
                    <div
                        className="relative h-3 overflow-hidden rounded bg-neutral-800"
                        title="Where your voice is in the clip"
                        data-testid="voice-strip"
                    >
                        {p.segments.map(([a, b], i) => (
                            <span
                                key={i}
                                className="absolute top-0 h-full bg-violet-500/80"
                                style={{
                                    left: `${(a / length) * 100}%`,
                                    width: `${((b - a) / length) * 100}%`,
                                }}
                            />
                        ))}
                        <span
                            className="absolute top-0 h-full w-px bg-white"
                            style={{ left: `${Math.min(100, Math.max(0, (t / length) * 100))}%` }}
                        />
                    </div>
                    <ul className="space-y-1">
                        {voiceovers.map((v, i) => (
                            <li
                                key={`${i}-${v.track_id}`}
                                className="flex flex-wrap items-center gap-2 text-xs"
                                data-testid="voice-placement"
                            >
                                <span className="min-w-24 font-medium text-neutral-200">
                                    {nameOf(v.track_id)}
                                </span>
                                <span className="text-neutral-500">starts at</span>
                                <input
                                    type="number"
                                    className="w-20 rounded border border-neutral-700 bg-neutral-900 px-1.5 py-0.5 tabular-nums"
                                    min={0}
                                    max={Math.max(0, length)}
                                    step={0.1}
                                    value={v.start}
                                    aria-label={`Voice ${i + 1} start`}
                                    onChange={(e) => {
                                        const x = Number(e.target.value);
                                        if (Number.isFinite(x))
                                            setPlacements(
                                                voiceovers.map((o, k) =>
                                                    k === i
                                                        ? {
                                                              ...o,
                                                              start: round(Math.min(600, Math.max(0, x))),
                                                          }
                                                        : o,
                                                ),
                                            );
                                    }}
                                />
                                <Button
                                    variant="ghost"
                                    className="!px-1.5 !py-0.5 !text-xs"
                                    onClick={() =>
                                        setPlacements(
                                            voiceovers.map((o, k) =>
                                                k === i ? { ...o, start: round(Math.max(0, t)) } : o,
                                            ),
                                        )
                                    }
                                >
                                    Move to playhead
                                </Button>
                                <Button
                                    variant="ghost"
                                    className="!px-1.5 !py-0.5 !text-xs"
                                    aria-label={`Remove voice ${i + 1}`}
                                    onClick={() => setPlacements(voiceovers.filter((_, k) => k !== i))}
                                >
                                    Remove
                                </Button>
                            </li>
                        ))}
                    </ul>
                    <div className="grid gap-3 sm:grid-cols-3">
                        <Field
                            label={`Video sound: ${audio.mute_original ? "muted" : signed(audio.original_db)}`}
                        >
                            <input
                                type="range"
                                className="w-full"
                                min={-30}
                                max={6}
                                step={1}
                                value={audio.original_db}
                                disabled={audio.mute_original}
                                aria-label="Video sound level"
                                onChange={(e) => setAudio({ original_db: Number(e.target.value) })}
                            />
                            <label className="mt-1 flex items-center gap-1 text-xs text-neutral-400">
                                <input
                                    type="checkbox"
                                    checked={audio.mute_original}
                                    onChange={(e) => setAudio({ mute_original: e.target.checked })}
                                />
                                Mute the video&apos;s sound
                            </label>
                        </Field>
                        <Field label={`Voice: ${signed(audio.voice_db)}`}>
                            <input
                                type="range"
                                className="w-full"
                                min={-12}
                                max={12}
                                step={1}
                                value={audio.voice_db}
                                aria-label="Voice level"
                                onChange={(e) => setAudio({ voice_db: Number(e.target.value) })}
                            />
                        </Field>
                        <Field label="Turn the video down while I speak">
                            <select
                                className="rounded border border-neutral-700 bg-neutral-900 px-1.5 py-1 text-xs"
                                value={audio.duck_db}
                                aria-label="Ducking"
                                disabled={audio.mute_original}
                                onChange={(e) => setAudio({ duck_db: Number(e.target.value) })}
                            >
                                {DUCK.map(([v, label]) => (
                                    <option key={v} value={v}>
                                        {label}
                                    </option>
                                ))}
                            </select>
                        </Field>
                    </div>
                    <p className="text-xs text-neutral-500">
                        The export levels your voice against the video&apos;s sound, then normalises the whole
                        clip; the preview plays both at their offsets, so its balance is close but not exact.
                    </p>
                </>
            )}

            {takes.length > 0 && (
                <details className="text-xs">
                    <summary className="cursor-pointer text-neutral-400">All takes ({takes.length})</summary>
                    <ul className="mt-2 space-y-1">
                        {takes.map((k) => (
                            <li key={k.id} className="flex items-center gap-2" data-testid="voice-take">
                                <span className="min-w-24">{k.name}</span>
                                <span className="tabular-nums text-neutral-500">
                                    {fmt(k.duration_ms / 1000, true)}
                                </span>
                                <Button
                                    variant="ghost"
                                    className="!px-1.5 !py-0.5 !text-xs"
                                    disabled={voiceovers.length >= 10}
                                    onClick={() =>
                                        setPlacements([
                                            ...voiceovers,
                                            { track_id: k.id, start: round(Math.max(0, t)) },
                                        ])
                                    }
                                >
                                    Place at playhead
                                </Button>
                                <Button
                                    variant="ghost"
                                    className="!px-1.5 !py-0.5 !text-xs"
                                    disabled={used.has(k.id) || remove.isPending}
                                    title={
                                        used.has(k.id) ? "Remove it from the clip and save first" : undefined
                                    }
                                    onClick={() => {
                                        if (confirm(`Delete ${k.name} from this computer?`))
                                            remove.mutate(k.id);
                                    }}
                                >
                                    Delete
                                </Button>
                            </li>
                        ))}
                    </ul>
                </details>
            )}
        </section>
    );
}

/** Plays placed takes in sync with the clip preview and ducks the video's sound like the export does. */
export function VoicePlayback({
    voiceovers,
    takes,
    audio,
    segments,
    t,
    playing,
    recording,
    onVideoVolume,
}: {
    voiceovers: VoiceoverEdit[];
    takes: VoiceTrack[];
    audio: AudioEdit;
    segments: Seg[];
    t: number;
    playing: boolean;
    recording: boolean;
    /** sets the preview video's volume (0..1) */
    onVideoVolume: (volume: number) => void;
}) {
    const refs = useRef<(HTMLAudioElement | null)[]>([]);
    useEffect(() => {
        const base = audio.mute_original ? 0 : Math.min(1, dbToGain(audio.original_db));
        onVideoVolume(Math.min(1, Math.max(0, base * duckGain(t, segments, audio.duck_db))));
        voiceovers.forEach((p, i) => {
            const el = refs.current[i];
            const take = takes.find((k) => k.id === p.track_id);
            if (!el || !take) return;
            const pos = t - p.start;
            el.volume = Math.min(1, dbToGain(audio.voice_db));
            if (playing && !recording && pos >= 0 && pos < take.duration_ms / 1000) {
                if (el.paused) {
                    el.currentTime = pos;
                    void el.play().catch(() => undefined);
                } else if (Math.abs(el.currentTime - pos) > 0.2) el.currentTime = pos;
            } else if (!el.paused) el.pause();
        });
    });
    return (
        <>
            {voiceovers.map((p, i) => (
                <audio
                    key={`${i}-${p.track_id}`}
                    ref={(el) => {
                        refs.current[i] = el;
                    }}
                    src={`/api/v1/voice/${p.track_id}/audio`}
                    preload="auto"
                    data-testid="voice-audio"
                />
            ))}
        </>
    );
}

const round = (v: number) => Math.round(v * 10) / 10;
const signed = (db: number) => (db === 0 ? "as recorded" : `${db > 0 ? "+" : ""}${db} dB`);
