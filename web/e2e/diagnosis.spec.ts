import { expect, test } from "@playwright/test";
import { API } from "./helpers";

// v4 plan row 2, on the dev script that reaches a probable cause: the diagnosis opens with no panel, so it shows only
// the conversation, the banner, the answer and the composer; the working before the answer folds into "How Smalls
// got here", written from the summariser's clauses; the panel toggles push the conversation over, and slide over it
// below 1100 px. Tagged @concluded: it runs in the second pass (E2E_SCRIPT=concluded).

async function diagnosis(request: import("@playwright/test").APIRequestContext): Promise<string> {
  const open = await (await request.get(`${API}/incidents/open`)).json();
  const { chat_id } = await (await request.post(`${API}/incidents/import`, { data: { number: open.incidents[0].number } })).json();
  await expect.poll(async () => (await (await request.get(`${API}/chats/${chat_id}`)).json()).events.some((e: { type: string }) => e.type === "outcomes"),
    { timeout: 30_000 }).toBe(true);
  return chat_id as string;
}

test("@concluded the diagnosis shows the conversation, the banner, the answer and the composer, and nothing else", async ({ page, request }) => {
  const id = await diagnosis(request);
  await page.goto(`/chats/${id}`);
  await expect(page.locator(".v-banner")).toContainText("Probable cause identified.");
  await expect(page.locator(".v-banner")).toContainText(/Confidence: High \(75%\)\. Recommended action: /);
  await expect(page.getByRole("link", { name: "Resolve incident…" })).toBeVisible();
  await expect(page.locator(".sidepanel")).toHaveCount(0);
  await expect(page.locator("#pending")).toHaveCount(0);
  await expect(page.locator(".lead")).toContainText("Probable cause: ");
  const rows = await page.locator(".ans-row dt").allInnerTexts();
  expect(rows.map((r) => r.toLowerCase())).toEqual(["why it fails", "recommended action", "confidence", "avoid"]);   // no group in the dev report
  const how = page.locator(".steps-card");
  await expect(how).toContainText(/How Smalls got here\s*\d+ checks · \d+ run/);
  await expect(how.locator(".steps-sum")).toContainText(/^Smalls .*\(scripted\)/);              // the summariser's clauses
  await expect(how.locator(".grp")).toHaveCount(0);                                              // the steps are folded away
  await how.getByRole("button", { name: /How Smalls got here/ }).click();
  await expect(how.locator(".grp").first()).toBeVisible();
  await expect(page.getByLabel("Message Smalls")).toBeVisible();
});

test("@concluded the panel toggles open and close one panel, pushing the conversation at 1440 px and over it at 900 px", async ({ page, request }) => {
  const id = await diagnosis(request);
  await page.goto(`/chats/${id}`);
  const picture = page.getByRole("button", { name: /Working picture/ });
  await picture.click();
  await expect(picture).toHaveAttribute("aria-pressed", "true");
  const panel = page.getByRole("complementary", { name: "Working picture" });
  await expect(panel).toContainText("Checks so far");
  expect(await panel.evaluate((el) => getComputedStyle(el).position)).not.toBe("fixed");
  await page.getByRole("button", { name: "Ticket", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "Ticket" })).toContainText("Work notes · newest first");
  await expect(page.locator(".sidepanel")).toHaveCount(1);
  await page.getByRole("button", { name: "Close panel" }).click();
  await expect(page.locator(".sidepanel")).toHaveCount(0);
  await page.setViewportSize({ width: 900, height: 900 });
  await picture.click();
  expect(await page.locator(".sidepanel").evaluate((el) => getComputedStyle(el).position)).toBe("fixed");
  await page.keyboard.press("Escape");
  await expect(page.locator(".sidepanel")).toHaveCount(0);
});

test("@concluded at phone width the diagnosis does not scroll sideways", async ({ page, request }) => {
  const id = await diagnosis(request);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/chats/${id}`);
  await expect(page.locator(".lead")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
