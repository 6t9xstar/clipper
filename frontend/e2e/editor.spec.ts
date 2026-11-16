// End-to-end flows against a real server (see e2e/server.mjs): import -> manual clip -> edit -> persist,
// and the optimistic-concurrency conflict path. AI analysis is not exercised here (it needs a speech model
// and real speech); its API is covered by backend tests.
import { expect, test, type Page } from "@playwright/test";
import { statSync } from "node:fs";
import { resolve } from "node:path";

const VIDEO = resolve(import.meta.dirname, "..", "test-results", "e2e-data", "pattern.mp4");

test.describe.configure({ mode: "serial" });

let clipUrl = "";

async function importVideo(page: Page): Promise<void> {
    await page.goto("/");
    await page.getByLabel("New project name").fill("E2E project");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByRole("heading", { name: "Import a video" })).toBeVisible();

    const importBtn = page.getByRole("button", { name: "Import" });
    await page.locator('input[type="file"]').first().setInputFiles(VIDEO);
    // rights confirmation is required
    await expect(importBtn).toBeDisabled();
    await page.locator("select").selectOption("other");
    await page.getByLabel("I confirm I have the right to use and edit this video.").check();
    await expect(importBtn).toBeDisabled(); // "other" needs an explanation
    await page.getByPlaceholder("e.g. licence name or who gave permission").fill("generated test pattern");
    await expect(importBtn).toBeEnabled();
    await importBtn.click();

    await expect(page.getByText("ready", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("1280×720")).toBeVisible();
}

test("import, make a clip, edit it and the edits persist", async ({ page }) => {
    await importVideo(page);
    await page.getByRole("link", { name: "Open" }).click();
    await expect(page).toHaveURL(/\/assets\/[0-9a-f]{32}$/);

    // deep link reload works (regression: /assets/<id> used to hit the static-file mount)
    await page.reload();
    const make = page.getByRole("button", { name: "+ Make a clip from the current position" });
    await expect(make).toBeVisible();
    await make.click();
    await expect(page).toHaveURL(/\/clips\/[0-9a-f]{32}$/);
    clipUrl = page.url();

    const range = page.getByTestId("range");
    await expect(range).toContainText("(20.0 s)");
    const save = page.getByRole("button", { name: "Saved" });
    await expect(save).toBeDisabled();

    // trim with the keyboard: start +2 s, end -1 s
    const start = page.getByRole("slider", { name: "Clip start" });
    await start.focus();
    await start.press("Shift+ArrowRight");
    await start.press("Shift+ArrowRight");
    const end = page.getByRole("slider", { name: "Clip end" });
    await end.focus();
    await end.press("Shift+ArrowLeft");
    await expect(range).toContainText("(17.0 s)");
    await expect(start).toHaveAttribute("aria-valuenow", "2");

    // caption style, framing, title
    const styles = page.getByRole("radiogroup", { name: "Caption style" });
    await styles.getByRole("radio", { name: /Gaming/ }).click();
    await expect(styles.getByRole("radio", { name: /Gaming/ })).toHaveAttribute("aria-checked", "true");
    await page.getByRole("radio", { name: "Whole frame on blur" }).click();
    await page.getByLabel("Title (for your own reference)").fill("Pattern test clip");

    // a chapter-title overlay: add it, type, show it, drag it down in the preview
    const overlays = page.getByRole("region", { name: "Text overlays" });
    await overlays.getByRole("button", { name: "+ Chapter title" }).click();
    await overlays.getByLabel("Overlay 1 text").fill("Pattern test");
    await overlays.getByRole("button", { name: "Show" }).click();
    const ov = page.getByTestId("overlay");
    await expect(ov).toHaveText(/PATTERN TEST/); // uppercase style, laid out by the server
    const line = ov.locator("div").first();
    const before = (await line.boundingBox())!;
    await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2);
    await page.mouse.down();
    await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2 + 120, { steps: 6 });
    await page.mouse.up();
    await expect.poll(async () => (await line.boundingBox())!.y).toBeGreaterThan(before.y + 80);

    // a voiceover take recorded from the (fake) microphone while the clip plays, placed automatically
    const voice = page.getByRole("region", { name: "Voiceover" });
    await voice.getByRole("button", { name: "● Record from the playhead" }).click();
    const stopRec = voice.getByRole("button", { name: /Stop/ });
    await expect(stopRec).toBeVisible();
    await page.waitForTimeout(3000);
    await stopRec.click();
    await expect(voice.getByTestId("voice-placement")).toHaveCount(1, { timeout: 30_000 });
    await expect(voice.getByTestId("voice-placement")).toContainText("Take 1");
    await expect(voice.getByLabel("Ducking")).toHaveValue("-12");

    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("button", { name: "Saved" })).toBeVisible();

    // everything survives a reload (comes back from the server)
    await page.reload();
    await expect(page.getByTestId("range")).toContainText("(17.0 s)");
    await expect(page.getByRole("slider", { name: "Clip start" })).toHaveAttribute("aria-valuenow", "2");
    await expect(
        page.getByRole("radiogroup", { name: "Caption style" }).getByRole("radio", { name: /Gaming/ }),
    ).toHaveAttribute("aria-checked", "true");
    await expect(page.getByRole("radio", { name: "Whole frame on blur" })).toHaveAttribute(
        "aria-checked",
        "true",
    );
    await expect(page.getByLabel("Title (for your own reference)")).toHaveValue("Pattern test clip");
    await expect(page.getByLabel("Overlay 1 text")).toHaveValue("Pattern test");
    const id = page.url().split("/").pop()!;
    const saved = (await (await page.request.get(`/api/v1/clips/${id}`)).json()) as {
        edit: { overlays: { y: number }[] };
    };
    expect(saved.edit.overlays[0]!.y).toBeGreaterThan(0.25); // the drag moved it down from 0.15
    await expect(page.getByTestId("voice-placement")).toHaveCount(1);

    // the 9:16 preview keeps its aspect ratio
    const box = await page.getByTestId("preview").boundingBox();
    expect(box).not.toBeNull();
    expect(Math.abs(box!.width / box!.height - 9 / 16)).toBeLessThan(0.01);
    await page.screenshot({ path: "test-results/editor.png", fullPage: true }); // for human review

    // export (draft quality: fastest), wait for the verified file, download it
    const panel = page.getByRole("region", { name: "Export" });
    await panel.getByRole("radio", { name: "Draft 720p" }).click();
    await panel.getByRole("button", { name: "Export video" }).click();
    const row = panel.getByTestId("export-row").first();
    await expect(row.getByText("done", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(row.getByTestId("export-facts")).toContainText("720×1280");
    await expect(row.getByTestId("export-facts")).toContainText("LUFS");
    const [dl] = await Promise.all([
        page.waitForEvent("download"),
        row.getByRole("link", { name: "Download MP4" }).click(),
    ]);
    expect(dl.suggestedFilename()).toMatch(/^pattern-test-clip-[0-9a-f]{6}\.mp4$/);
    const exportId = (await row.getByRole("link", { name: "Details" }).getAttribute("href"))!.split("/")[4]!;
    const manifest = (await (await page.request.get(`/api/v1/exports/${exportId}/files/json`)).json()) as {
        voiceover: { takes: { name: string }[] } | null;
        overlays: { text: string }[];
    };
    expect(manifest.voiceover?.takes.map((t) => t.name)).toEqual(["Take 1"]);
    expect(manifest.overlays[0]!.text).toBe("PATTERN TEST");
    const { size } = statSync((await dl.path())!);
    expect(size).toBeGreaterThan(20_000);
    // editing again makes the export "from an earlier version" and blocks a new export until saved
    await page.getByLabel("Title (for your own reference)").fill("Pattern test clip v2");
    await expect(panel.getByRole("button", { name: "Export video" })).toBeDisabled();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(row.getByText("made from an earlier version of this clip")).toBeVisible();
    await page.screenshot({ path: "test-results/export.png", fullPage: true });
});

test("a change made elsewhere is not overwritten", async ({ page }) => {
    test.skip(!clipUrl, "needs the clip from the previous test");
    await page.goto(clipUrl);
    const title = page.getByLabel("Title (for your own reference)");
    await expect(title).toHaveValue("Pattern test clip v2");

    // another tab saves first (same cookie, so the API accepts it)
    const id = clipUrl.split("/").pop()!;
    const current = await page.request.get(`/api/v1/clips/${id}`);
    expect(current.ok()).toBeTruthy();
    const { version } = (await current.json()) as { version: number };
    const other = await page.request.patch(`/api/v1/clips/${id}`, {
        data: { title: "Changed in another tab" },
        headers: { "if-match": `"${version}"` },
    });
    expect(other.status()).toBe(200);

    await title.fill("My stale change");
    await page.getByRole("button", { name: "Save changes" }).click();
    const alert = page.getByRole("alert").filter({ hasText: "changed elsewhere" });
    await expect(alert).toBeVisible();

    await page.getByRole("button", { name: /Load the latest version/ }).click();
    await expect(title).toHaveValue("Changed in another tab");
    await expect(page.getByRole("alert").filter({ hasText: "changed elsewhere" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Saved" })).toBeVisible();
});

test("the API refuses requests without the access token", async ({ playwright, baseURL }) => {
    const anon = await playwright.request.newContext({ baseURL });
    const r = await anon.get("/api/v1/projects");
    expect(r.status()).toBe(401);
    expect(((await r.json()) as { code: string }).code).toBe("TOKEN_REQUIRED");
    await anon.dispose();
});
