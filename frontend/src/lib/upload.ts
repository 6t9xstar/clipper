// Resumable chunked upload (protocol: docs/api.md, "Importing a video").
import { ApiError } from "./api";
import type { Asset } from "./types";

export const CHUNK = 16 * 1024 * 1024; // below the server's 64 MiB cap; small enough for smooth progress

export interface UploadOptions {
    onProgress?: (sent: number, total: number) => void;
    signal?: AbortSignal;
    chunkSize?: number;
    /** test seam */
    fetchImpl?: typeof fetch;
    /** test seam */
    sleep?: (ms: number) => Promise<void>;
}

async function problem(r: Response): Promise<ApiError> {
    let body: Record<string, unknown> = {};
    try {
        body = (await r.json()) as Record<string, unknown>;
    } catch {
        /* not JSON */
    }
    return new ApiError(r.status, body);
}

/** Sends `file` to an asset in `uploading` state, starting at the server's offset. Retries network errors a
 *  few times with backoff; on OFFSET_MISMATCH it continues from the offset the server reports. */
export async function uploadFile(asset: Asset, file: Blob, o: UploadOptions = {}): Promise<Asset> {
    const f = o.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
    const sleep = o.sleep ?? ((ms: number) => new Promise<void>((res) => setTimeout(res, ms)));
    const size = o.chunkSize ?? CHUNK;
    const url = `/api/v1/assets/${asset.id}/upload`;
    if (file.size !== asset.size_bytes) {
        throw new ApiError(400, {
            code: "FILE_MISMATCH",
            title: "This is not the same file.",
            hint: "Choose the file you started uploading (same size).",
        });
    }
    const st = await f(url, { credentials: "same-origin", signal: o.signal });
    if (!st.ok) throw await problem(st);
    let offset = ((await st.json()) as { offset: number }).offset;
    let failures = 0;
    let last: Asset = asset;
    o.onProgress?.(offset, file.size);
    while (offset < file.size) {
        const chunk = file.slice(offset, Math.min(offset + size, file.size));
        let r: Response;
        try {
            r = await f(url, {
                method: "PUT",
                body: chunk,
                headers: { "upload-offset": String(offset), "content-type": "application/octet-stream" },
                credentials: "same-origin",
                signal: o.signal,
            });
        } catch (e) {
            if (o.signal?.aborted) throw e;
            if (++failures > 5) throw e;
            await sleep(500 * 2 ** failures);
            const again = await f(url, { credentials: "same-origin", signal: o.signal });
            if (again.ok) offset = ((await again.json()) as { offset: number }).offset;
            continue;
        }
        if (r.status === 409) {
            const err = await problem(r);
            if (err.code === "OFFSET_MISMATCH" && typeof err.body.offset === "number") {
                offset = err.body.offset;
                continue;
            }
            throw err;
        }
        if (!r.ok) throw await problem(r);
        failures = 0;
        last = (await r.json()) as Asset;
        offset = last.received_bytes;
        o.onProgress?.(offset, file.size);
    }
    return last;
}
