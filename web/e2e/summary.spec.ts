import { expect, test } from "@playwright/test";
import { API, newChat, until } from "./helpers";

// Row R13: the resolve dialog and the summary. Continue is disabled until there is a note; the agent's path keeps
// every record and the commander's own path none; unticking a signal's last record shows "Left out"; saving, then
// any edit, unsaves; Regenerate bumps the version.

async function finishedChat(page: import("@playwright/test").Page, request: import("@playwright/test").APIRequestContext) {
  const id = await newChat(request, { origin: "headless" });
  await until(request, id, ["finished"]);
  await page.goto(`/chats/${id}`);
  return id;
}

test("Continue stays disabled until there is a note, then opens the summary", async ({ page, request }) => {
  const id = await finishedChat(page, request);
  await page.getByRole("link", { name: "Resolve incident…" }).click();
  const dialog = page.getByRole("dialog", { name: "Resolve incident" });
  const go = dialog.getByRole("button", { name: "Continue to resolution review" });
  await expect(go).toBeDisabled();
  await dialog.getByLabel("Resolution notes").fill("Cleaned up and ran the job again; it finished.");
  await expect(go).toBeEnabled();
  // The scripted report is inconclusive, so "Smalls' conclusion was right" cannot be chosen and the dialog starts on the
  // commander's own path.
  await expect(dialog.getByRole("radio", { name: /Smalls’ diagnosis was right/ })).toBeDisabled();
  await go.click();
  await expect(page).toHaveURL(new RegExp(`/chats/${id}/resolution$`));
  await expect(page.getByRole("heading", { name: "In short" })).toBeVisible();
  await expect(page.locator(".short-row", { hasText: "Resolution code" })).toContainText("Solved (Work Around)");
});

test("Mark resolved waits for a report, then appears without a reload", async ({ page, request }) => {
  const id = await newChat(request, { origin: "chat", auto_approve: false });
  await until(request, id, ["waiting"]);
  await page.goto(`/chats/${id}`);
  await expect(page.locator("#pending")).toBeVisible();
  await expect(page.getByText("Resolve incident…")).toHaveCount(0);
  await page.goto(`/chats/${id}?resolve`);                                    // the dialog's address opens nothing either
  await expect(page.locator("#pending")).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Resolve incident" })).toHaveCount(0);
  const header = page.getByRole("link", { name: "Resolve incident…" });
  for (let round = 0; round < 12 && !(await header.isVisible()); round++) {
    const gate = page.locator("#pending");
    await expect(gate.or(header).first()).toBeVisible({ timeout: 30_000 });
    if (await header.isVisible()) break;
    await expect(gate.getByRole("button", { name: /^Approve( all)?/ }).first()).toBeFocused();
    const before = await gate.innerText();
    await page.keyboard.press("Shift+A");
    // The next gate takes this one's place, so wait for a change rather than for the gate to go.
    await expect.poll(async () => (await gate.count()) === 0 || (await gate.innerText()) !== before, { timeout: 30_000 }).toBe(true);
  }
  await expect(header).toBeVisible({ timeout: 30_000 });
});

test("the agent's path keeps every record the report cites", async ({ page, request }) => {
  const id = await finishedChat(page, request);
  await request.put(`${API}/chats/${id}/resolution`, { data: { path: "agent", note: "Rolled back and re-ran.", resolved_at: "02:40", resolved_by: "Commander" } });
  await page.goto(`/chats/${id}/resolution`);
  await page.getByRole("button", { name: /^Evidence/ }).click();
  const boxes = page.locator(".rail input[type=checkbox]");
  expect(await boxes.count()).toBeGreaterThan(0);
  for (const b of await boxes.all()) await expect(b).toBeChecked();
});

test("the commander's own path starts with nothing kept, and a signal with no record kept is left out", async ({ page, request }) => {
  const id = await finishedChat(page, request);
  const r = await request.put(`${API}/chats/${id}/resolution`, { data: { path: "own", note: "Fixed it by hand.", resolved_at: "02:40", resolved_by: "Commander" } });
  expect(r.ok()).toBe(true);
  await page.goto(`/chats/${id}/resolution`);
  await expect(page.locator(".rail")).toBeVisible();                                     // Evidence starts open on this path
  for (const b of await page.locator(".rail input[type=checkbox]").all()) await expect(b).not.toBeChecked();
  await page.getByRole("button", { name: /^How to recognise it next time/ }).click();
  const signals = page.locator("[data-signal]");
  if (await signals.count()) await expect(signals.first()).toContainText("Left out");
  await page.getByRole("button", { name: "Keep all" }).click();
  if (await signals.count()) await expect(signals.first()).not.toContainText("Left out");
  if (await signals.count()) {
    const handle = await signals.first().locator("button.sm-handle").first().getAttribute("data-handle");
    await page.locator(".evrow").filter({ has: page.locator(`button.sm-handle[data-handle="${handle}"]`) }).locator("input").uncheck();
    await expect(signals.first()).toContainText("Left out");
  }
});

test("saving puts it in the knowledge base, any edit unsaves it, and Regenerate bumps the version", async ({ page, request }) => {
  const id = await finishedChat(page, request);
  await request.put(`${API}/chats/${id}/resolution`, { data: { path: "agent", note: "Rolled back and re-ran.", resolved_at: "02:40", resolved_by: "Commander" } });
  await page.goto(`/chats/${id}/resolution`);
  await expect(page.locator(".label", { hasText: "draft 1" })).toBeVisible();
  await page.getByRole("button", { name: "Save as knowledge article" }).click();
  await expect(page.locator(".actbar strong", { hasText: "Saved as a knowledge article" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Draft communications →" })).toBeVisible();
  await page.getByRole("button", { name: /^Evidence/ }).click();
  await page.locator(".rail input[type=checkbox]").first().uncheck();
  await expect(page.getByRole("button", { name: "Save as knowledge article" })).toBeVisible();
  await page.getByRole("button", { name: "Regenerate" }).click();
  await expect(page.locator(".label", { hasText: "draft 2" })).toBeVisible();
  await expect(page.getByText(/Draft 2, regenerated/)).toBeVisible();
});

test("the summary never scrolls sideways, however long a record's text", async ({ page, request }) => {
  const id = await finishedChat(page, request);
  await request.put(`${API}/chats/${id}/resolution`, { data: { path: "own", note: "Ran the job again; it finished.", resolved_at: "02:40", resolved_by: "Commander" } });
  for (const width of [1440, 900, 420]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/chats/${id}/resolution`);
    await expect(page.getByRole("button", { name: "Save as knowledge article" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), `at ${width}px`).toBeLessThanOrEqual(0);
  }
});
