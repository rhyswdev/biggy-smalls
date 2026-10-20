import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { API, newChat, until } from "./helpers";

// Row R14: the stakeholder messages. The scripted drafter's draft goes through the same checks as any text, failing
// first, updated on each keystroke; Copy writes the clipboard and marks that exact text copied, an edit clears it and a
// reload keeps it; Restore draft is enabled only after a change; the knowledge-base preview finds the saved entry by
// its terms.

test.use({ permissions: ["clipboard-read", "clipboard-write"] });

async function finished(request: APIRequestContext): Promise<string> {
  const id = await newChat(request, { origin: "headless" });
  await until(request, id, ["finished"]);
  return id;
}

async function toMessages(page: Page, request: APIRequestContext): Promise<string> {
  const id = await finished(request);
  await request.put(`${API}/chats/${id}/resolution`, { data: { path: "own", note: "Restarted the job by hand; it finished.", resolved_at: "02:40", resolved_by: "Commander" } });
  await page.goto(`/chats/${id}/resolution`);
  await page.getByRole("button", { name: "Save as knowledge article" }).click();
  await page.getByRole("button", { name: "Draft communications →" }).click();
  await expect(page).toHaveURL(new RegExp(`/chats/${id}/communications$`));
  return id;
}

const box = (page: Page) => page.getByRole("textbox", { name: "Senior stakeholders" });
const firstCheck = (page: Page) => page.locator(".checks .chk").first();

test("each draft is checked as it is typed, failing first, and Restore draft comes back after a change", async ({ page, request }) => {
  await toMessages(page, request);
  await expect(page.getByRole("heading", { name: "Major incident communications" })).toBeVisible();
  await expect(page.getByText("0 of 4 messages copied")).toBeVisible();
  await expect(page.locator(".tab", { hasText: "Draft" })).toHaveCount(4);
  await expect(box(page)).toHaveValue(/Scripted draft for Senior stakeholders/);
  await expect(page.getByText("Drafted by the scripted profile.")).toBeVisible();
  // The dev report says nothing about customers and gives no next update: those checks fail and come first.
  await expect(firstCheck(page)).toHaveClass(/bad/);
  const restore = page.getByRole("button", { name: "Restore draft" });
  await expect(restore).toBeDisabled();

  await box(page).fill("Customers saw errors for a while. Resolved at 02:40 UTC. Next update 03:10 UTC.");
  await expect(page.locator(".checks .chk.bad")).toHaveCount(0);
  await expect(firstCheck(page)).toHaveClass(/open/);                    // no owner in the report: checked by hand
  await box(page).press("End");
  await page.keyboard.type(" **");
  await expect(firstCheck(page)).toHaveClass(/bad/);
  await expect(firstCheck(page)).toContainText("Plain text: no markdown");
  await page.keyboard.press("Backspace");
  await page.keyboard.press("Backspace");
  await expect(page.locator(".checks .chk.bad")).toHaveCount(0);
  await page.keyboard.type(" See log:host-1/0a1b2c3d.");
  await expect(firstCheck(page)).toContainText("No evidence handles");
  await expect(page.locator(".tab", { hasText: "Senior stakeholders" })).toContainText("Edited");

  await expect(restore).toBeEnabled();
  await restore.click();
  await expect(box(page)).toHaveValue(/Scripted draft for Senior stakeholders/);
  await expect(restore).toBeDisabled();
});

test("Copy writes the clipboard and marks that exact text copied; an edit clears it", async ({ page, request }) => {
  await toMessages(page, request);
  await page.getByRole("button", { name: "Status page" }).click();
  const status = page.getByRole("textbox", { name: "Status page" });
  await page.getByRole("button", { name: "Copy message", exact: true }).click();
  await expect(page.getByRole("button", { name: "Copied", exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await status.inputValue());
  await expect(page.getByText("1 of 4 messages copied")).toBeVisible();
  await expect(page.locator(".prog-bar span.on")).toHaveCount(1);
  await expect(page.locator(".tab", { hasText: "Status page" })).toContainText("✓ Copied");

  await page.reload();                                                     // kept by the API, by the text's hash
  await expect(page.getByText("1 of 4 messages copied")).toBeVisible();
  await page.getByRole("button", { name: "Status page" }).click();
  await page.getByRole("textbox", { name: "Status page" }).press("End");
  await page.keyboard.type(" More.");
  await expect(page.getByRole("button", { name: "Copy message", exact: true })).toBeVisible();
  await expect(page.getByText("0 of 4 messages copied")).toBeVisible();
  await expect(page.locator(".tab", { hasText: "Status page" })).toContainText("Edited");
  await page.waitForTimeout(900);                                          // the edit is saved after a pause in typing
  await page.reload();
  await expect(page.getByText("0 of 4 messages copied")).toBeVisible();
  await page.getByRole("button", { name: "Status page" }).click();
  await expect(page.getByRole("textbox", { name: "Status page" })).toHaveValue(/ More\.$/);
});

test("the knowledge-base entry shows what was saved and is found by its own terms", async ({ page, request }) => {
  await toMessages(page, request);
  await page.getByRole("button", { name: /Knowledge article/ }).click();
  const entry = page.getByRole("article", { name: "The knowledge base entry" });
  await expect(entry).toBeVisible();
  await expect(entry.getByRole("heading", { level: 2 })).toHaveText("Restarted the job by hand; it finished.");
  await expect(page.getByText("What would bring it up")).toBeVisible();
  await expect(page.locator(".term").first()).toBeVisible();
  await expect(page.getByText(/^(First of \d+ match(es)?|Match \d+ of \d+) for these terms/)).toBeVisible();
});

test("opening the communications drafts every message not drafted yet, without asking", async ({ page, request }) => {
  const id = await finished(request);
  await request.put(`${API}/chats/${id}/resolution`, { data: { path: "own", note: "Restarted the job by hand; it finished.", resolved_at: "02:40", resolved_by: "Commander" } });
  await page.goto(`/chats/${id}/communications`);
  await expect(page.locator(".tab", { hasText: "Not drafted" })).toHaveCount(0, { timeout: 20_000 });
  await expect(page.getByRole("textbox", { name: "Senior stakeholders" })).toHaveValue(/Scripted draft for Senior stakeholders/);
  const view = await (await request.get(`${API}/chats/${id}/messages`)).json();
  expect(view.messages).toHaveLength(4);
});
