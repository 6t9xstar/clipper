// Starts a throwaway Clipper server for the Playwright suite: fresh data dir, generated test media (no
// third-party content), the built UI.
//
//   pattern.mp4  FFmpeg test pattern + tone (always)
//   speech.mp4   test pattern + speech from the operating system's own text-to-speech, for the full
//                import -> analyse -> edit -> export flow. Made only when a TTS engine is available
//                (Windows SAPI, macOS `say`, Linux `espeak-ng`); otherwise that test is skipped.
//   CLIPPER_E2E_MODELS=<folder>  copy an already-downloaded speech model folder instead of downloading
//                                "tiny" (~75 MB) from Hugging Face on first analysis.
import { spawn, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..", "..");
const work = resolve(import.meta.dirname, "..", "test-results", "e2e-data");
const port = process.env.CLIPPER_E2E_PORT ?? "8799";

rmSync(work, { recursive: true, force: true });
mkdirSync(join(work, "data"), { recursive: true });

function ffmpeg(...args) {
    const r = spawnSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], {
        stdio: "inherit",
    });
    if (r.status !== 0) {
        console.error("ffmpeg failed; is it on PATH? (see README requirements)");
        process.exit(1);
    }
}

// prettier-ignore
ffmpeg(
    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=20",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", join(work, "pattern.mp4"),
);

const SCRIPT =
    "Why do most new runners get injured in the first three weeks? Here is the mistake. " +
    "They increase their distance by thirty percent every single week. " +
    "The rule that works is ten percent per week, and your knees will thank you for it. " +
    "That one change cut injuries in half in our running club last year. " +
    "So start slow, stay consistent, and enjoy the run.";

function speak(out) {
    if (process.platform === "win32") {
        const ps =
            "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; " +
            `$s.Rate = 0; $s.SetOutputToWaveFile('${out.replace(/'/g, "''")}'); ` +
            `$s.Speak('${SCRIPT.replace(/'/g, "''")}'); $s.Dispose()`;
        return spawnSync("powershell", ["-NoProfile", "-Command", ps]).status === 0;
    }
    if (process.platform === "darwin") {
        const aiff = out.replace(/\.wav$/, ".aiff");
        if (spawnSync("say", ["-o", aiff, SCRIPT]).status !== 0) return false;
        return spawnSync("ffmpeg", ["-v", "error", "-y", "-i", aiff, out]).status === 0;
    }
    return spawnSync("espeak-ng", ["-w", out, SCRIPT]).status === 0;
}

const wav = join(work, "speech.wav");
if (speak(wav) && existsSync(wav)) {
    // prettier-ignore
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-i", wav,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", join(work, "speech.mp4"),
    );
} else {
    writeFileSync(join(work, "no-speech.txt"), "no text-to-speech engine available\n");
}

if (process.env.CLIPPER_E2E_MODELS) {
    cpSync(resolve(process.env.CLIPPER_E2E_MODELS), join(work, "data", "models"), { recursive: true });
}

const child = spawn("uv", ["run", "--directory", join(root, "backend"), "clipper", "serve"], {
    stdio: "inherit",
    env: {
        ...process.env,
        CLIPPER_DATA_DIR: join(work, "data"),
        CLIPPER_PORT: port,
        CLIPPER_LOG_JSON: "false",
        CLIPPER_ASR_MODEL: "tiny",
        CLIPPER_ASR_DEVICE: "cpu",
    },
});
const stop = () => child.kill();
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
process.on("exit", stop);
child.on("exit", (code) => process.exit(code ?? 0));
