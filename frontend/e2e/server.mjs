// Starts a throwaway Clipper server for the Playwright suite: fresh data dir, a generated test video
// (FFmpeg test pattern + tone, made here so no third-party media is involved), the built UI.
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..", "..");
const work = resolve(import.meta.dirname, "..", "test-results", "e2e-data");
const port = process.env.CLIPPER_E2E_PORT ?? "8799";

rmSync(work, { recursive: true, force: true });
mkdirSync(work, { recursive: true });

const video = join(work, "pattern.mp4");
const ff = spawnSync(
    "ffmpeg",
    [
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=30:duration=20",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=20",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        video,
    ],
    { stdio: "inherit" },
);
if (ff.status !== 0) {
    console.error("ffmpeg failed; is it on PATH? (see docs/installation.md)");
    process.exit(1);
}

const child = spawn("uv", ["run", "--directory", join(root, "backend"), "clipper", "serve"], {
    stdio: "inherit",
    env: {
        ...process.env,
        CLIPPER_DATA_DIR: join(work, "data"),
        CLIPPER_PORT: port,
        CLIPPER_LOG_JSON: "false",
        CLIPPER_ASR_MODEL: "tiny",
    },
});
const stop = () => child.kill();
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
process.on("exit", stop);
child.on("exit", (code) => process.exit(code ?? 0));
