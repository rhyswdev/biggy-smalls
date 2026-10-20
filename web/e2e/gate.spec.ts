import { expect, test, type Page } from "@playwright/test";
import { API, newChat, until } from "./helpers";

// Row R10: the approval gate's keyboard model, now in the v4 tray above the composer. Each test starts a chat that
// asks before every check, so it stops at the dev script's own first three checks.

async function waitingChat(page: Page, request: Parameters<typeof newChat>[0]): Promise<string> {
  const id = await newChat(request, { origin: "chat", auto_approve: false });
  await until(request, id, ["waiting"]);
  await page.goto(`/chats/${id}`);
  await expect(page.locator("#pending")).toBeVisible();
  await expect(page.locator("#pending [data-kf] button").first()).toBeFocused();      // focus lands on the target Approve
  return id;
}

// The dev script's first turn: what is alerting, what changed and what people are saying, chosen by its "model".
const FIRST = ["call_101_list_alerts", "call_102_list_changes", "call_103_read_chat"];
const gate = (page: Page) => page.locator("#pending");
const target = (page: Page) => page.locator("#pending [data-kf] button").first();
const rows = (page: Page) => page.locator("#pending .trow");
const receipts = (page: Page) => page.locator("#pending .rcpt[data-call]");

test("A approves the read in focus, and focus moves to the next waiting read's Approve", async ({ page, request }) => {
  await waitingChat(page, request);
  await expect(target(page)).toBeFocused();
  await expect(target(page)).toHaveAttribute("aria-keyshortcuts", "A");
  await expect(target(page)).toHaveAttribute("title", "Approve (A)");
  const first = await rows(page).first().getAttribute("data-call");
  await page.keyboard.press("a");
  await expect(page.locator(`.rcpt[data-call="${first}"]`)).toContainText("Decided");
  await expect(rows(page)).toHaveCount(2);
  await expect(target(page)).toBeFocused();
  await expect(gate(page).locator(".tray-title")).toHaveText("◆ Smalls wants to run 2 checks");
});

test("S skips; R opens the reason, Enter needs one, Esc cancels back to that read's Approve", async ({ page, request }) => {
  await waitingChat(page, request);
  await page.keyboard.press("s");
  await expect(receipts(page).first()).toContainText("Skipped");
  await expect(target(page)).toBeFocused();
  const second = await rows(page).first().getAttribute("data-call");
  await page.keyboard.press("r");
  const reason = page.getByLabel("Reason for rejecting");
  await expect(reason).toBeFocused();
  await page.keyboard.press("Enter");                                    // no reason: nothing happens
  await expect(reason).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(reason).toBeHidden();
  await expect(page.locator(`.trow[data-call="${second}"] [data-kf] button`).first()).toBeFocused();
  await page.keyboard.press("r");
  await page.getByLabel("Reason for rejecting").fill("Already known from the ticket");
  await page.keyboard.press("Enter");
  await expect(page.locator(`.rcpt[data-call="${second}"]`)).toContainText("Rejected by you: “Already known from the ticket”");
  await expect(target(page)).toBeFocused();
});

test("E edits a read; Esc cancels, Enter approves with the edit, and the edited read runs", async ({ page, request }) => {
  const id = await waitingChat(page, request);
  await page.keyboard.press("e");
  const since = page.locator("#pending .sub-form input").first();
  await expect(since).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator("#pending .sub-form")).toHaveCount(0);
  await expect(target(page)).toBeFocused();
  await page.keyboard.press("e");
  const field = page.locator("#pending .sub-form label").filter({ hasText: /^since/ }).locator("input");
  await field.fill("-90m");
  await field.press("Enter");
  await expect(receipts(page).first()).toContainText("Decided");
  await expect(target(page)).toBeFocused();
  await page.keyboard.press("Shift+A");
  await expect.poll(async () => {
    const view = await (await request.get(`${API}/chats/${id}`)).json();
    return view.events.some((e: { type: string; data: { edited_args?: { since?: string } } }) => e.type === "gate_decision" && e.data.edited_args?.since === "-90m");
  }, { timeout: 20_000 }).toBe(true);
});

test("keys are ignored while typing, with Ctrl, Cmd or Alt, and on repeat", async ({ page, request }) => {
  await waitingChat(page, request);
  await page.getByLabel("Message Smalls").click();
  await page.keyboard.type("as");
  await expect(rows(page)).toHaveCount(3);
  await expect(page.getByLabel("Message Smalls")).toHaveValue("as");
  await target(page).focus();
  for (const k of ["Control+a", "Meta+a", "Alt+a"]) await page.keyboard.press(k);
  await expect(rows(page)).toHaveCount(3);
  await target(page).evaluate((el) => el.dispatchEvent(new KeyboardEvent("keydown", { key: "a", repeat: true, bubbles: true })));
  await expect(rows(page)).toHaveCount(3);
});

test("Shift+A approves all the waiting reads, and the run goes on to the next gate", async ({ page, request }) => {
  const id = await waitingChat(page, request);
  const all = page.getByRole("button", { name: "Approve all 3" });
  await expect(all).toHaveAttribute("aria-keyshortcuts", "Shift+A");
  await gate(page).focus();
  await page.keyboard.press("Shift+A");
  await expect(page.locator(".grp-title").first()).toHaveText(/^3 checks · 3 run/, { timeout: 20_000 });
  await until(request, id, ["waiting"]);
  await expect(gate(page)).toBeVisible({ timeout: 20_000 });
  await expect(target(page)).toBeFocused();
});

test("turning auto-approve on keeps the decisions already made on the waiting gate", async ({ page, request }) => {
  const id = await waitingChat(page, request);
  const first = await rows(page).first().getAttribute("data-call");
  await page.keyboard.press("r");
  await page.getByLabel("Reason for rejecting").fill("Already known from the ticket");
  await page.keyboard.press("Enter");
  await expect(page.locator(`.rcpt[data-call="${first}"]`)).toContainText("Rejected by you");
  await page.getByRole("switch", { name: "Auto-approve checks" }).click();
  // The rejection stands; the reads nobody had decided are approved, and the run goes on.
  await expect.poll(async () => {
    const view = await (await request.get(`${API}/chats/${id}`)).json();
    return view.events
      .filter((e: { type: string; data: { call_id?: string } }) => e.type === "gate_decision" && FIRST.includes(e.data.call_id ?? ""))
      .map((e: { data: { call_id?: string; decision?: string } }) => `${e.data.call_id === first ? "first" : "other"}:${e.data.decision}`)
      .sort();
  }, { timeout: 20_000 }).toEqual(["first:rejected", "other:approved", "other:approved"]);
});

test("an action left waiting is named as one in the composer's hint", async ({ page, request }) => {
  const id = await newChat(request, { origin: "chat", auto_approve: true });
  await until(request, id, ["waiting"]);
  await page.goto(`/chats/${id}`);
  await expect(page.locator("#pending .tray-title")).toHaveText("◆ Smalls proposes 1 action");
  await expect(page.locator(".wait-link")).toHaveText(/1 action is waiting for you above/);
});

test("Shift+S skips all the waiting reads", async ({ page, request }) => {
  await waitingChat(page, request);
  await page.keyboard.press("Shift+S");
  await expect(page.locator(".grp-title").first()).toHaveText(/^3 checks · 3 skipped/, { timeout: 20_000 });
});

test("while reads wait, Send is disabled and the hint is an amber link to them", async ({ page, request }) => {
  await waitingChat(page, request);
  await page.getByLabel("Message Smalls").fill("a note");
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();
  const link = page.locator(".wait-link");
  await expect(link).toHaveText(/3 checks are waiting for you above/);
  await expect(link).toHaveAttribute("href", "#pending");
});

test("a double submit from two tabs records one decision", async ({ browser, page, request }) => {
  const id = await waitingChat(page, request);
  const other = await (await browser.newContext()).newPage();
  await other.goto(`/chats/${id}`);
  await expect(other.locator("#pending [data-kf] button").first()).toBeFocused();
  await Promise.all([page.keyboard.press("Shift+A"), other.keyboard.press("Shift+S")]);
  await expect.poll(async () => (await page.locator(".sm-notice-error").allTextContents()).concat(await other.locator(".sm-notice-error").allTextContents()).join(" "),
    { timeout: 20_000 }).toContain("already answered");
  const view = await (await request.get(`${API}/chats/${id}`)).json();
  const resumed = view.events.filter((e: { type: string; data: { name?: string } }) => e.type === "phase" && e.data.name === "resumed");
  const sweep = view.events.filter((e: { type: string; data: { call_id?: string; by?: { who?: string } } }) =>
    e.type === "gate_decision" && FIRST.includes(e.data.call_id ?? "") && e.data.by?.who === "human");
  expect(resumed.length).toBeGreaterThanOrEqual(1);
  expect(sweep).toHaveLength(3);                                          // one decision per card, from one tab
  expect(new Set(sweep.map((e: { data: { decision?: string } }) => e.data.decision)).size).toBe(1);
});

test.describe("on a touch device", () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 900, height: 900 } });
  test("the key legend is hidden", async ({ page, request }) => {
    await waitingChat(page, request);
    await expect(page.locator("#pending .keys")).toBeHidden();
  });
});
