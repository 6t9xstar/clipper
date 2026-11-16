import { defineConfig, devices } from "@playwright/test";

// Runs against the built UI served by a real `clipper serve` (npm run build first).
// Uses an installed browser channel instead of downloading one: Edge locally, Chrome on CI.
const port = Number(process.env.CLIPPER_E2E_PORT ?? 8799);
const channel = process.env.CLIPPER_E2E_CHANNEL ?? (process.env.CI ? "chrome" : "msedge");

export default defineConfig({
    testDir: "e2e",
    timeout: 90_000,
    expect: { timeout: 10_000 },
    fullyParallel: false,
    workers: 1,
    retries: 0,
    reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
    use: {
        baseURL: `http://127.0.0.1:${port}`,
        screenshot: "only-on-failure",
        trace: "retain-on-failure",
    },
    projects: [
        {
            name: channel,
            use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1280, height: 900 } },
        },
    ],
    webServer: {
        command: "node e2e/server.mjs",
        url: `http://127.0.0.1:${port}/healthz`,
        timeout: 120_000,
        reuseExistingServer: false,
        stdout: "pipe",
        stderr: "pipe",
    },
});
