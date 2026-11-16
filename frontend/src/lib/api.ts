// Thin fetch wrapper. Authentication is the HttpOnly cookie the server set when this page loaded, so no token
// is ever visible to JavaScript. Errors are RFC 9457 problem+json with a stable `code` and a `hint`.

export class ApiError extends Error {
    status: number;
    code: string;
    hint?: string;
    body: Record<string, unknown>;

    constructor(status: number, body: Record<string, unknown>) {
        super(typeof body.title === "string" ? body.title : `Request failed (${status})`);
        this.status = status;
        this.code = typeof body.code === "string" ? body.code : "HTTP_ERROR";
        this.hint = typeof body.hint === "string" ? body.hint : undefined;
        this.body = body;
    }
}

export interface Opts {
    method?: string;
    body?: unknown;
    ifMatch?: number;
    signal?: AbortSignal;
}

export async function api<T>(path: string, opts: Opts = {}): Promise<T> {
    const headers: Record<string, string> = {};
    if (opts.body !== undefined) headers["content-type"] = "application/json";
    if (opts.ifMatch !== undefined) headers["if-match"] = `"${opts.ifMatch}"`;
    let r: Response;
    try {
        r = await fetch(`/api/v1${path}`, {
            method: opts.method ?? "GET",
            headers,
            body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
            credentials: "same-origin",
            signal: opts.signal,
        });
    } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") throw e;
        throw new ApiError(0, {
            code: "NETWORK",
            title: "Cannot reach the Clipper server.",
            hint: "Check that clipper serve is still running.",
        });
    }
    if (r.status === 204) return undefined as T;
    const text = await r.text();
    let body: unknown;
    try {
        body = text ? JSON.parse(text) : null;
    } catch {
        body = { title: text.slice(0, 200) };
    }
    if (!r.ok) throw new ApiError(r.status, (body ?? {}) as Record<string, unknown>);
    return body as T;
}

export function errorText(e: unknown): { title: string; hint?: string } {
    if (e instanceof ApiError) return { title: e.message, hint: e.hint };
    if (e instanceof Error) return { title: e.message };
    return { title: String(e) };
}
