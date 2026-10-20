import { expect, test } from "@playwright/test";
import { API } from "./helpers";

// v4 plan row 4: a new chat hides the sidebar behind a toggle and centres its composer; attaching is off; a chat
// can be linked to an imported incident before it is sent, and once sent it is the diagnosis of that incident.

test("the sidebar is hidden until the toggle shows it, and attaching is not offered yet", async ({ page }) => {
  await page.goto("/chats/new");
  const nav = page.getByRole("navigation", { name: "Smalls" });
  await expect(nav).toHaveCount(0);
  await page.getByRole("button", { name: "Show menu" }).click();
  await expect(nav).toBeVisible();
  await page.getByRole("button", { name: "Hide menu" }).click();
  await expect(nav).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Attach logs or a screenshot" })).toBeDisabled();
  await expect(page.getByRole("switch", { name: "Auto-approve checks" })).toBeVisible();
});

test("a chat linked to an imported incident becomes that incident's chat", async ({ page, request }) => {
  const open = await (await request.get(`${API}/incidents/open`)).json();
  const number = open.incidents[0].number as string;
  await request.post(`${API}/incidents/import`, { data: { number } });
  await page.goto("/chats/new");
  await page.getByLabel("Link this chat to an incident").selectOption(number);
  await page.getByLabel("Message Smalls").fill("What changed in the last hour?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page).toHaveURL(/\/chats\/[0-9a-f]+$/);
  await expect(page.locator(".inc-meta")).toContainText(number);
  const id = page.url().split("/").pop()!;
  expect((await (await request.get(`${API}/chats/${id}`)).json()).chat.incident).toBe(number);
});

test("at phone width the new chat does not scroll sideways", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/chats/new");
  await expect(page.getByRole("heading", { name: "What’s going on?" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
