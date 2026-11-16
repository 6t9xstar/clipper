import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router";
import "./index.css";
import { ApiError } from "./lib/api";
import { AssetPage } from "./pages/AssetPage";
import { ClipEditor } from "./pages/ClipEditor";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";

const qc = new QueryClient({
    defaultOptions: {
        queries: {
            retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 2,
            refetchOnWindowFocus: false,
        },
    },
});

function Shell() {
    return (
        <div className="min-h-screen">
            <header className="border-b border-neutral-800 bg-neutral-950/80">
                <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-2.5">
                    <Link to="/" className="flex items-center gap-2 font-semibold">
                        <img src="/favicon.svg" alt="" className="h-6 w-6" />
                        Clipper
                    </Link>
                    <span className="text-xs text-neutral-500">pre-alpha · runs on this computer only</span>
                </div>
            </header>
            <main className="mx-auto max-w-7xl px-4 py-6">
                <Routes>
                    <Route path="/" element={<ProjectsPage />} />
                    <Route path="/projects/:projectId" element={<ProjectPage />} />
                    <Route path="/assets/:assetId" element={<AssetPage />} />
                    <Route path="/clips/:clipId" element={<ClipEditor />} />
                    <Route path="*" element={<p className="text-neutral-400">Page not found.</p>} />
                </Routes>
            </main>
        </div>
    );
}

createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <QueryClientProvider client={qc}>
            <BrowserRouter>
                <Shell />
            </BrowserRouter>
        </QueryClientProvider>
    </StrictMode>,
);
