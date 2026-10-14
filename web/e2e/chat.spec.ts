import { expect, test } from "@playwright/test";
import { API, newChat, until } from "./helpers";

// Row R12: a follow-up after the answer continues the same run, with nothing read again for it; the suggested questions are
// answered from the report; the headless and blank origins show what started them.

test("a free-text follow-up on a finished run continues the same run, below the first answer", async ({ page, request }) => {
  const id = await newChat(request, { origin: "headless" });
  await until(request, id, ["finished"]);
  const before = await (await request.get(`${API}/chats/${id}`)).json();
  await page.goto(`/chats/${id}`);
  await page.getByLabel("Message Smalls").fill("Why do you think that?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".msg-sm").filter({ hasText: "answered from what it already had" })).toBeVisible({ timeout: 20_000 });
  const after = await (await request.get(`${API}/chats/${id}`)).json();
  expect(after.runs.map((r: { run_id: string }) => r.run_id)).toEqual(before.runs.map((r: { run_id: string }) => r.run_id));
  const harnessReads = (v: { events: { type: string; data: { by?: string } }[] }) => v.events.filter((e) => e.type === "tool_call_proposed" && e.data.by === "harness").length;
  expect(harnessReads(after)).toBe(harnessReads(before));
  const order = await page.locator(".convo > *").evaluateAll((els) => els.map((e) => e.textContent ?? ""));
  const report = order.findIndex((t) => t.includes("Findings"));
  const answer = order.findIndex((t) => t.includes("answered from what it already had"));
  expect(report).toBeGreaterThan(-1);
  expect(answer).toBeGreaterThan(report);
});

test("a suggested question is answered from the report, with no new reads", async ({ page, request }) => {
  const id = await newChat(request, { origin: "headless" });
  await until(request, id, ["finished"]);
  await page.goto(`/chats/${id}`);
  const before = (await (await request.get(`${API}/chats/${id}`)).json()).events.length;
  await page.getByRole("button", { name: "What would change your mind?" }).click();
  await expect(page.getByText("From the report. No new reads.")).toBeVisible();
  expect((await (await request.get(`${API}/chats/${id}`)).json()).events.length).toBe(before);
  await expect(page.getByRole("button", { name: "What would change your mind?" })).toHaveCount(0);
});

test("an agent run's chat opens with its banner and the ticket that started it", async ({ page, request }) => {
  const id = await newChat(request, { origin: "headless" });
  await until(request, id, ["finished"]);
  await page.goto(`/chats/${id}`);
  await expect(page.locator(".origin")).toContainText(/Agent run · finished in .* · \d+ reads?, none needed approval/);
  await expect(page.locator(".tkt")).toContainText("Triggered this run");
  await expect(page.getByRole("link", { name: "Back to dashboard" })).toBeVisible();
});

test("a new chat is not linked to an incident until it is sent", async ({ page }) => {
  await page.goto("/chats/new");
  await expect(page.locator(".inc")).toContainText("Not linked to an incident yet");
  await expect(page.getByRole("heading", { name: "New chat" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();
  await page.getByLabel("Message Smalls").fill("Jobs on the batch hosts keep failing since this morning.");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page).toHaveURL(/\/chats\/[0-9a-f]+$/);
  await expect(page.locator(".msg-ic").first()).toContainText("Jobs on the batch hosts keep failing");
});

test("Investigate in chat loads the commander's note to send as it is", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Investigate in chat" }).click();
  await expect(page).toHaveURL(/\/chats\/new\?from=incident$/);
  const box = page.getByLabel("Message Smalls");
  await expect(box).not.toHaveValue("");
  await page.getByRole("button", { name: "Start investigation" }).click();
  await expect(page).toHaveURL(/\/chats\/[0-9a-f]+$/);
  await expect(page.locator("#pending")).toBeVisible({ timeout: 20_000 });          // reads ask by default
});
