import { expect, test } from "@playwright/test";
import { API, newChat, until } from "./helpers";

// Row R12: a follow-up after the answer continues the same run, with nothing read again for it; the headless and blank
// origins show what started them. Enter sends, and the end of the conversation stays in view while Smalls answers.

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

test("an agent run's chat opens with its banner and the ticket that started it", async ({ page, request }) => {
  const id = await newChat(request, { origin: "headless" });
  await until(request, id, ["finished"]);
  await page.goto(`/chats/${id}`);
  await expect(page.locator(".now-sub")).toContainText(/Diagnosis started automatically · .* · \d+ checks? · follow-up checks need your approval/);
  await expect(page.locator(".tkt")).toContainText("Started automatically");
  await expect(page.getByRole("navigation", { name: "Smalls" })).toBeVisible();
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
  await page.goto("/chats/new?from=incident");
  const box = page.getByLabel("Message Smalls");
  await expect(box).not.toHaveValue("");
  await page.getByRole("button", { name: "Start investigation" }).click();
  await expect(page).toHaveURL(/\/chats\/[0-9a-f]+$/);
  await expect(page.locator("#pending")).toBeVisible({ timeout: 20_000 });          // reads ask by default
});

test("Enter sends, Shift+Enter starts a new line, and the end of the conversation stays in view while Smalls answers", async ({ page, request }) => {
  const id = await newChat(request, { origin: "headless" });
  await until(request, id, ["finished"]);
  await page.setViewportSize({ width: 1440, height: 600 });
  await page.goto(`/chats/${id}`);
  const gap = () => page.evaluate(() => { const m = document.querySelector(".mainc")!; return m.scrollHeight - m.clientHeight - m.scrollTop; });
  await expect(page.locator(".lead")).toBeVisible();
  await page.getByRole("button", { name: "Business impact, technical detail and evidence" }).click();
  await page.getByRole("button", { name: /How Smalls got here/ }).click();                     // the steps, unfolded
  const closed = page.locator(".steps-card .grp > .grp-sum[aria-expanded=false]");
  for (let n = await closed.count(); n > 0; n--) await closed.first().click();
  await page.evaluate(() => document.querySelector(".mainc")!.scrollTo({ top: 0 }));
  expect(await gap()).toBeGreaterThan(200);                       // longer than the window, and still at the top
  const box = page.getByLabel("Message Smalls");
  await box.fill("Why do you think that?");
  await box.press("Shift+Enter");
  await box.pressSequentially("And what next?");
  await expect(box).toHaveValue("Why do you think that?\nAnd what next?");
  await box.press("Enter");
  await expect(box).toHaveValue("");
  await expect(page.locator(".msg-sm").filter({ hasText: "answered from what it already had" })).toBeVisible({ timeout: 20_000 });
  await expect.poll(gap).toBeLessThan(4);
});

test("Enter starts a new chat, and does nothing while the box is empty", async ({ page }) => {
  await page.goto("/chats/new");
  const box = page.getByLabel("Message Smalls");
  await box.press("Enter");
  await expect(page).toHaveURL(/\/chats\/new$/);
  await box.fill("Jobs on the batch hosts keep failing since this morning.");
  await box.press("Enter");
  await expect(page).toHaveURL(/\/chats\/[0-9a-f]+$/);
});
