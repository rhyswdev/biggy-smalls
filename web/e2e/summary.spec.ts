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
  await page.getByRole("link", { name: "Mark resolved…" }).click();
  const dialog = page.getByRole("dialog", { name: "Mark it resolved" });
  const go = dialog.getByRole("button", { name: "Continue to summary" });
  await expect(go).toBeDisabled();
  await dialog.getByLabel("How was it resolved?").fill("Cleaned up and ran the job again; it finished.");
  await expect(go).toBeEnabled();
  // The scripted report is inconclusive, so "Smalls' conclusion was right" cannot be chosen and the dialog starts on the
  // commander's own path.
  await expect(dialog.getByRole("radio", { name: /Smalls’ conclusion was right/ })).toBeDisabled();
  await go.click();
  await expect(page).toHaveURL(new RegExp(`/chats/${id}/summary$`));
  await expect(page.locator('nav[aria-label="Incident steps"] [aria-current="step"]')).toContainText("Summary");
  await expect(page.locator('nav[aria-label="Incident steps"] a').first()).toContainText("✓");
});

test("the agent's path keeps every record the report cites", async ({ page, request }) => {
  const id = await finishedChat(page, request);
  await request.put(`${API}/chats/${id}/resolution`, { data: { path: "agent", note: "Rolled back and re-ran.", resolved_at: "02:40", resolved_by: "Commander" } });
  await page.goto(`/chats/${id}/summary`);
  const boxes = page.locator(".rail input[type=checkbox]");
  expect(await boxes.count()).toBeGreaterThan(0);
  for (const b of await boxes.all()) await expect(b).toBeChecked();
});

test("the commander's own path starts with nothing kept, and a signal with no record kept is left out", async ({ page, request }) => {
  const id = await finishedChat(page, request);
  const r = await request.put(`${API}/chats/${id}/resolution`, { data: { path: "own", note: "Fixed it by hand.", resolved_at: "02:40", resolved_by: "Commander" } });
  expect(r.ok()).toBe(true);
  await page.goto(`/chats/${id}/summary`);
  for (const b of await page.locator(".rail input[type=checkbox]").all()) await expect(b).not.toBeChecked();
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
  await page.goto(`/chats/${id}/summary`);
  await expect(page.locator(".label", { hasText: "draft 1" })).toBeVisible();
  await page.getByRole("button", { name: "Save to knowledge base" }).click();
  await expect(page.getByText("✓ Saved to the knowledge base")).toBeVisible();
  await expect(page.getByRole("button", { name: "Draft stakeholder messages" })).toBeVisible();
  await page.locator(".rail input[type=checkbox]").first().uncheck();
  await expect(page.getByRole("button", { name: "Save to knowledge base" })).toBeVisible();
  await page.getByRole("button", { name: "Regenerate" }).click();
  await expect(page.locator(".label", { hasText: "draft 2" })).toBeVisible();
  await expect(page.getByText(/Draft 2, regenerated/)).toBeVisible();
});
