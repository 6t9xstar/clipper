import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { MAX_LEN, MIN_LEN, moveHandle, snapTo } from "../components/Timeline";
import { ApiError } from "./api";
import { activeGroup, px } from "./captions";
import { cropRect } from "./framing";
import { bytes, fmt } from "./time";
import type { Asset, CaptionGroup } from "./types";
import { uploadFile } from "./upload";

describe("framing", () => {
    it("matches the backend on the shared vectors (preview == export)", () => {
        const file = resolve(import.meta.dirname, "../../../testdata/framing_cases.json");
        const { cases } = JSON.parse(readFileSync(file, "utf-8")) as {
            cases: { src: [number, number]; x: number; y: number; zoom: number; rect: number[] }[];
        };
        expect(cases.length).toBeGreaterThanOrEqual(30);
        for (const c of cases) {
            const r = cropRect(c.src[0], c.src[1], c.x, c.y, c.zoom);
            [r.x, r.y, r.w, r.h].forEach((v, i) => expect(v).toBeCloseTo(c.rect[i]!, 3));
        }
    });
    it("rejects empty sources", () => {
        expect(() => cropRect(0, 10)).toThrow();
    });
});

describe("captions", () => {
    const groups: CaptionGroup[] = [
        { s: 0, e: 1, lines: [], fit: [] },
        { s: 1, e: 2.5, lines: [], fit: [] },
        { s: 4, e: 5, lines: [], fit: [] },
    ];
    it("finds the visible group, including gaps", () => {
        expect(activeGroup(groups, 0)).toBe(groups[0]);
        expect(activeGroup(groups, 1)).toBe(groups[1]);
        expect(activeGroup(groups, 2.49)).toBe(groups[1]);
        expect(activeGroup(groups, 3)).toBeUndefined();
        expect(activeGroup(groups, 5)).toBeUndefined();
        expect(activeGroup([], 1)).toBeUndefined();
    });
    it("scales output pixels to the preview", () => {
        expect(px(96, 960)).toBe(48);
    });
});

describe("trim", () => {
    const words = [
        { w: "a", s: 1.0, e: 1.4 },
        { w: "b", s: 2.0, e: 2.6 },
    ];
    it("snaps to nearby word edges only", () => {
        expect(snapTo(1.9, words, "s")).toBe(2.0);
        expect(snapTo(2.7, words, "e")).toBe(2.6);
        expect(snapTo(5, words, "s")).toBe(5);
    });
    it("keeps the clip valid", () => {
        expect(moveHandle("start", 9.9, 0, 10, 100)).toEqual([10 - MIN_LEN, 10]);
        expect(moveHandle("end", 0.1, 5, 10, 100)).toEqual([5, 5 + MIN_LEN]);
        expect(moveHandle("end", 2000, 5, 10, 100)).toEqual([5, 100]);
        expect(moveHandle("start", -3, 5, 10, 100)).toEqual([0, 10]);
        expect(moveHandle("start", 0, 700, 1000, 2000)).toEqual([1000 - MAX_LEN, 1000]);
    });
});

describe("format", () => {
    it("formats times and sizes", () => {
        expect(fmt(0)).toBe("0:00");
        expect(fmt(75.25, true)).toBe("1:15.3");
        expect(fmt(3725)).toBe("1:02:05");
        expect(bytes(1536)).toBe("1.5 KB");
        expect(bytes(3 * 1024 ** 3)).toBe("3.0 GB");
    });
});

describe("upload", () => {
    const asset = { id: "a1", size_bytes: 10, received_bytes: 0 } as Asset;
    const json = (status: number, body: unknown) =>
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

    it("resumes from the server offset, follows OFFSET_MISMATCH and retries network errors", async () => {
        const file = new Blob([new Uint8Array(10)]);
        const calls: string[] = [];
        let failedOnce = false;
        let mismatched = false;
        let received = 0;
        const fetchImpl = (async (_url: RequestInfo | URL, init?: RequestInit) => {
            if (!init?.method) {
                calls.push(`GET ${received}`);
                return json(200, { offset: received });
            }
            const off = Number((init.headers as Record<string, string>)["upload-offset"]);
            const size = (init.body as Blob).size;
            calls.push(`PUT ${off}+${size}`);
            if (off === 4 && !failedOnce) {
                failedOnce = true;
                received = 6; // the server got part of it before the connection dropped
                throw new TypeError("network");
            }
            if (off === 6 && !mismatched) {
                mismatched = true;
                received = 8;
                return json(409, { code: "OFFSET_MISMATCH", offset: 8 });
            }
            received = off + size;
            return json(200, { ...asset, received_bytes: received });
        }) as typeof fetch;
        const progress: number[] = [];
        const done = await uploadFile(asset, file, {
            fetchImpl,
            chunkSize: 4,
            sleep: async () => {},
            onProgress: (s) => progress.push(s),
        });
        expect(done.received_bytes).toBe(10);
        expect(calls).toEqual(["GET 0", "PUT 0+4", "PUT 4+4", "GET 6", "PUT 6+4", "PUT 8+2"]);
        expect(progress.at(-1)).toBe(10);
    });

    it("refuses a different file", async () => {
        await expect(uploadFile(asset, new Blob([new Uint8Array(3)]))).rejects.toBeInstanceOf(ApiError);
    });
});
