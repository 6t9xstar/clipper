/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Dev server (npm run dev) proxies /api to a running `clipper serve`. The browser never sees the access
// token: the proxy adds it, read from the data folder (CLIPPER_DATA_DIR, default ../data).
const backend = process.env.CLIPPER_URL ?? "http://127.0.0.1:8765";
function devToken(): string {
    const dir = process.env.CLIPPER_DATA_DIR ?? resolve(import.meta.dirname, "../data");
    try {
        return readFileSync(resolve(dir, "api_token"), "ascii").trim();
    } catch {
        return "";
    }
}

export default defineConfig({
    plugins: [react(), tailwindcss()],
    build: {
        outDir: resolve(import.meta.dirname, "../backend/clipper/web/dist"),
        emptyOutDir: true,
        assetsDir: "static", // not "assets": /assets/:id is an app route
        sourcemap: false,
    },
    server: {
        port: 5173,
        strictPort: true,
        proxy: {
            "/api": {
                target: backend,
                changeOrigin: true,
                configure(proxy) {
                    proxy.on("proxyReq", (req) => {
                        req.setHeader("x-clipper-token", devToken());
                        req.removeHeader("origin"); // same-origin from the backend's point of view
                    });
                },
            },
        },
    },
    test: {
        environment: "jsdom",
        include: ["src/**/*.test.{ts,tsx}"],
    },
});
