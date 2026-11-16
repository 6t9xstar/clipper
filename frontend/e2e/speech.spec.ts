// The full flow on speech: import -> real speech recognition (faster-whisper "tiny") -> suggested clip ->
// caption preview -> export -> sidecar captions. Uses speech made by the OS text-to-speech (e2e/server.mjs);
// skipped when this machine has none. The first run downloads the "tiny" model (~75 MB) unless
// CLIPPER_E2E_MODELS points at a models folder.
import { expect, test } from "@playwright/test";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const SPEECH = resolve(import.meta.dirname, "..", "test-results", "e2e-data", "speech.mp4");

test("speech video: find clips, caption preview, export with captions", async ({ page }) => {
    test.skip(!existsSync(SPEECH), "no text-to-speech engine on this machine (see e2e/server.mjs)");
    test.setTimeout(420_000);

    await page.goto("/");
    await page.getByLabel("New project name").fill("Speech E2E");
    await page.getByRole("button", { name: "Create" }).click();
    await page.locator('input[type="file"]').first().setInputFiles(SPEECH);
    await page.getByLabel("I confirm I have the right to use and edit this video.").check();
    await page.getByRole("button", { name: "Import" }).click();
    await expect(page.getByText("ready", { exact: true })).toBeVisible({ timeout: 60_000 });
    await page.getByRole("link", { name: "Open" }).click();

    await page.getByLabel("Min s").fill("5");
    await page.getByLabel("Max s").fill("20");
    await page.getByRole("button", { name: "Find clips", exact: true }).click();
    const cards = page.getByTestId("clip-card");
    await expect(cards.first()).toBeVisible({ timeout: 300_000 });

    await cards.first().getByRole("link", { name: "Edit" }).click();
    await expect(page).toHaveURL(/\/clips\/[0-9a-f]{32}$/);
    // jump to a word in the transcript: the caption overlay shows it in the bundled font
    const word = page.getByTestId("transcript").getByRole("button").nth(2);
    await word.click();
    const caption = page.getByTestId("caption");
    await expect(caption).toBeVisible();
    await expect(caption.locator("div").first()).toHaveCSS("font-family", /Clipper Montserrat Black/);
    expect(await page.evaluate(() => document.fonts.check('80px "Clipper Montserrat Black"'))).toBe(true);

    const panel = page.getByRole("region", { name: "Export" });
    await panel.getByRole("button", { name: "Export video" }).click();
    const row = panel.getByTestId("export-row").first();
    await expect(row.getByText("done", { exact: true })).toBeVisible({ timeout: 120_000 });
    await expect(row.getByTestId("export-facts")).toContainText("1080×1920");

    const srtHref = await row.getByRole("link", { name: "SRT" }).getAttribute("href");
    const srt = await (await page.request.get(srtHref!)).text();
    expect(srt).toMatch(/^1\r?\n00:00:0\d,\d{3} --> /);
    expect(srt.toLowerCase()).toMatch(/run|week|knee|club|percent/);
    await page.screenshot({ path: "test-results/speech-export.png", fullPage: true });
});
