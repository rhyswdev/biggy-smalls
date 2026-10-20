import { expect, test } from "@playwright/test";
import { API } from "./helpers";

// v4 plan section 8, the whole flow, on the dev script that reaches a probable cause: import, the diagnosis starts on
// its own, the overview shows its result, open the diagnosis, ask a follow-up, approve its check from the floating
// tray with its key, resolve with a resolution code, review, save as a knowledge article, open the communications
// (drafted on arrival) and copy one. Tagged @concluded: it runs in the second pass.

test.use({ permissions: ["clipboard-read", "clipboard-write"] });

test("@concluded import to copied communications", async ({ page, request }) => {
  test.setTimeout(120_000);
  const open = await (await request.get(`${API}/incidents/open`)).json();
  const fresh = open.incidents.find((i: { imported: boolean }) => !i.imported) ?? open.incidents[0];
  await page.goto("/");
  if (!fresh.imported && (await page.getByRole("heading", { name: "It’s quiet in here" }).count())) {
    await page.getByLabel("Incident number").fill(fresh.number);
    await page.getByRole("button", { name: "Import and diagnose" }).click();
  } else {
    await request.post(`${API}/incidents/import`, { data: { number: fresh.number } });
    await page.goto("/");
  }
  const panel = page.getByRole("region", { name: "Smalls diagnosis" });
  await expect(panel.getByText("Probable cause", { exact: true })).toBeVisible({ timeout: 45_000 });
  await panel.getByRole("link", { name: "Open diagnosis" }).click();
  await expect(page).toHaveURL(/\/chats\/[0-9a-f]+$/);
  await expect(page.locator(".v-banner")).toContainText("Probable cause identified.");

  await page.getByLabel("Message Smalls").fill("Anything new in the last few minutes?");
  await page.keyboard.press("Enter");
  const tray = page.locator("#pending");
  await expect(tray).toBeVisible({ timeout: 30_000 });
  await expect(tray.locator(".tray-title")).toHaveText("◆ Smalls wants to run 1 check");
  await expect(tray.locator("[data-kf] button").first()).toBeFocused();
  await page.keyboard.press("a");
  await expect(page.locator(".msg-sm").filter({ hasText: "answered from what it already had" })).toBeVisible({ timeout: 30_000 });

  await page.getByRole("link", { name: "Resolve incident…" }).click();
  const dialog = page.getByRole("dialog", { name: "Resolve incident" });
  await dialog.getByLabel("Resolution code").selectOption("Solved (Permanently)");
  await dialog.getByLabel("Resolution notes").fill("Undid the most recent change; errors back to baseline.");
  await dialog.getByRole("button", { name: "Continue to resolution review" }).click();
  await expect(page).toHaveURL(/\/resolution$/);
  await expect(page.locator(".short-row", { hasText: "Resolution code" })).toContainText("Solved (Permanently)");
  await page.getByRole("button", { name: "Save as knowledge article" }).click();
  await page.getByRole("button", { name: "Draft communications →" }).click();
  await expect(page).toHaveURL(/\/communications$/);
  await page.getByRole("button", { name: /^Status page/ }).click();
  await page.getByRole("button", { name: "Copy message", exact: true }).click();
  await expect(page.getByText("1 of 4 messages copied")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await page.getByRole("textbox", { name: "Status page" }).inputValue());
});
