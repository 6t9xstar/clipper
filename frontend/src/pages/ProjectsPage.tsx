import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { Button, ErrorBox, inputCls } from "../components/ui";
import { api } from "../lib/api";
import type { Page, Project } from "../lib/types";

export function ProjectsPage() {
    const qc = useQueryClient();
    const nav = useNavigate();
    const [name, setName] = useState("");
    const projects = useQuery({
        queryKey: ["projects"],
        queryFn: () => api<Page<Project>>("/projects?limit=200"),
    });
    const create = useMutation({
        mutationFn: (n: string) => api<Project>("/projects", { method: "POST", body: { name: n } }),
        onSuccess: (p) => {
            void qc.invalidateQueries({ queryKey: ["projects"] });
            void nav(`/projects/${p.id}`);
        },
    });
    const remove = useMutation({
        mutationFn: (id: string) => api<void>(`/projects/${id}`, { method: "DELETE" }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
    });

    return (
        <div className="space-y-6">
            <section>
                <h1 className="text-2xl font-semibold">Projects</h1>
                <p className="mt-1 text-sm text-neutral-400">
                    A project holds the videos you import and the clips you make from them. Everything stays
                    on this computer.
                </p>
            </section>

            <form
                className="flex max-w-lg gap-2"
                onSubmit={(e) => {
                    e.preventDefault();
                    if (name.trim()) create.mutate(name.trim());
                }}
            >
                <input
                    className={inputCls}
                    placeholder="New project name"
                    aria-label="New project name"
                    value={name}
                    maxLength={200}
                    onChange={(e) => setName(e.target.value)}
                />
                <Button type="submit" variant="primary" disabled={!name.trim() || create.isPending}>
                    Create
                </Button>
            </form>
            <ErrorBox error={create.error} />

            {projects.isLoading && <p className="text-sm text-neutral-400">Loading…</p>}
            <ErrorBox error={projects.error} />
            {projects.data && projects.data.items.length === 0 && (
                <p className="text-sm text-neutral-400">No projects yet.</p>
            )}
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {projects.data?.items.map((p) => (
                    <li
                        key={p.id}
                        className="flex items-center justify-between rounded-lg border border-neutral-800 p-4"
                    >
                        <Link to={`/projects/${p.id}`} className="font-medium hover:text-violet-300">
                            {p.name}
                        </Link>
                        <Button
                            variant="ghost"
                            aria-label={`Delete project ${p.name}`}
                            onClick={() => {
                                if (
                                    confirm(
                                        `Delete "${p.name}" and all its videos and clips from this computer?`,
                                    )
                                )
                                    remove.mutate(p.id);
                            }}
                        >
                            Delete
                        </Button>
                    </li>
                ))}
            </ul>
            <ErrorBox error={remove.error} />
        </div>
    );
}
