import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Job } from "./types";

export const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

/** Polls a job once a second until it ends. */
export function useJob(id: string | null | undefined) {
    return useQuery({
        queryKey: ["job", id],
        queryFn: () => api<Job>(`/jobs/${id}`),
        enabled: !!id,
        refetchInterval: (q) => (q.state.data && TERMINAL.has(q.state.data.status) ? false : 1000),
    });
}
