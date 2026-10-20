import { expect, test } from "@playwright/test";
import { API } from "./helpers";

// v4 plan row 1, the shell: with nothing imported `/` is the first-run screen with no sidebar; an unknown number
// says so; picking an open ticket imports it and lands on its overview with the sidebar, whose Resolution and
// Communications wait "after resolving"; the import dialog marks what is already imported; the ticket drawer shows
// its notes in UTC with the source's local time beside. The suites share one API, so the first-run checks run only
// while nothing is imported yet; every test after that works either way.

async function imported(request: import("@playwright/test").APIRequestContext): Promise<{ number: string; chat_id: string }[]> {
  return (await request.get(`${API}/incidents`)).json();
}

test("first run: no sidebar, an unknown number says so, an open ticket imports and lands on the overview", async ({ page, request }) => {
  if (!(await imported(request)).length) {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "It’s quiet in here" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Smalls" })).toHaveCount(0);
    await expect(page.getByText("✓ ServiceNow connected")).toBeVisible();
    await page.getByLabel("Incident number").fill("INC0000001");
    await page.getByRole("button", { name: "Import and diagnose" }).click();
    await expect(page.locator(".v-first [role=alert]")).toContainText("INC0000001");
    await expect(page).toHaveURL(/\/$/);
    const row = page.locator(".v-first button.recent").first();
    await expect(row).toContainText("not yet in Smalls");
    await row.click();
  } else {
    await page.goto("/");
  }
  await expect(page.getByRole("navigation", { name: "Smalls" })).toBeVisible();
  const steps = page.locator(".v-sb .sb-items").first();
  await expect(steps.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  await expect(steps.getByRole("link", { name: "Diagnosis" })).toBeVisible();
  const [first] = await imported(request);
  const status = (await (await request.get(`${API}/chats/${first!.chat_id}`)).json()).chat.status;
  if (status !== "resolved") {
    await expect(steps.locator(".sb-it.off")).toHaveCount(2);
    await expect(steps.getByText("after resolving")).toHaveCount(2);
  }
  await expect(page.locator(".inc-meta")).toContainText(first!.number);
});

test("the import dialog marks a ticket already imported, and a known number lands on its overview", async ({ page, request }) => {
  const open = await (await request.get(`${API}/incidents/open`)).json();
  const number = open.incidents[0].number as string;
  await request.post(`${API}/incidents/import`, { data: { number } });
  await page.goto("/");
  await page.getByRole("button", { name: "New incident" }).click();
  await expect(page.getByRole("menuitem", { name: /Start a chat/ })).toBeFocused();
  await page.getByRole("menuitem", { name: /Import from service desk/ }).click();
  const dialog = page.getByRole("dialog", { name: "Import from service desk" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".recent").first()).toContainText("already imported");
  await dialog.getByLabel("Ticket number or link").fill("INC0000001");
  await dialog.getByRole("button", { name: "Import and investigate" }).click();
  await expect(dialog.getByRole("alert")).toContainText("INC0000001");
  await dialog.getByLabel("Ticket number or link").fill(number);
  await dialog.getByRole("button", { name: "Import and investigate" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".inc-meta")).toContainText(number);
  expect((await imported(request)).filter((i) => i.number === number)).toHaveLength(1);
  await page.keyboard.press("Escape");
});

test("the ticket drawer shows the work notes newest first, in UTC with local time beside, and Escape closes it", async ({ page, request }) => {
  const open = await (await request.get(`${API}/incidents/open`)).json();
  await request.post(`${API}/incidents/import`, { data: { number: open.incidents[0].number } });
  await page.goto("/");
  await page.getByRole("button", { name: "View ticket" }).click();
  const drawer = page.locator(".v-drawer");
  await expect(drawer.getByText("ServiceNow ticket, as imported")).toBeVisible();
  await expect(drawer.getByText(/Times converted from .+ to UTC\./)).toBeVisible();
  const heads = drawer.locator(".note-head");
  expect(await heads.count()).toBeGreaterThan(0);
  await expect(heads.first()).toContainText(/\d\d:\d\d:\d\d UTC \(\d\d:\d\d:\d\d [A-Z+\-0-9]+\)/);
  const times = await heads.locator(".time").allInnerTexts();
  expect([...times].sort().reverse()).toEqual(times);
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(page.getByRole("button", { name: "View ticket" })).toBeFocused();
});

test("at phone width the sidebar stacks above the page and nothing scrolls sideways", async ({ page, request }) => {
  const open = await (await request.get(`${API}/incidents/open`)).json();
  await request.post(`${API}/incidents/import`, { data: { number: open.incidents[0].number } });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Smalls" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const nav = await page.getByRole("navigation", { name: "Smalls" }).boundingBox();
  const head = await page.locator(".inc-title").boundingBox();
  expect(nav!.y).toBeLessThan(head!.y);
});

test("the overview loads with nothing missing: no failed request and no console error", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("response", (r) => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  await page.goto("/");
  await expect(page.locator("main")).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(errors).toEqual([]);
});
