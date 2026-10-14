import { expect, test } from "@playwright/test";
import { newChat, until } from "./helpers";

// Row R8: the dashboard's agent panel follows a run live, a reload mid-run restores it, and a chat waiting on the
// commander shows amber with Resume as the one primary button.

test("Run agent goes from idle to running to finished, live", async ({ page }) => {
  await page.goto("/");
  const panel = page.locator(".inc-side");
  const start = panel.getByRole("button", { name: /^(Run agent|Run again)$/ });
  await start.click();
  await expect(panel.getByText("Agent running")).toBeVisible();
  await expect(panel.locator(".step").first()).toBeVisible();
  await expect(panel.getByText("Finished")).toBeVisible({ timeout: 45_000 });
  await expect(panel.getByRole("link", { name: "Open findings in chat" })).toBeVisible();
  await expect(panel.getByText(/action proposed, not approved/)).toBeVisible();          // an unattended run defers its action
});

test("a reload mid-run restores the panel", async ({ page }) => {
  await page.goto("/");
  const panel = page.locator(".inc-side");
  await panel.getByRole("button", { name: /^(Run agent|Run again)$/ }).click();
  await expect(panel.getByText("Agent running")).toBeVisible();
  await page.reload();
  await expect(panel.getByText("Agent running").or(panel.getByText("Finished"))).toBeVisible();
  await expect(panel.getByText("Finished")).toBeVisible({ timeout: 45_000 });
});

test("a chat waiting on reads is amber, with Resume as the primary button", async ({ page, request }) => {
  const id = await newChat(request, { origin: "chat", auto_approve: false });
  await until(request, id, ["waiting"]);
  await page.goto("/");
  const row = page.locator(".chat-row").filter({ has: page.locator(`a[href="/chats/${id}#pending"]`) });
  await expect(row.locator(".sm-tag-attention")).toHaveText(/Waiting on 3 reads/);
  const resume = row.getByRole("link", { name: "Resume" });
  await expect(resume).toHaveClass(/primary/);
  await expect(page.locator(".chat-row .lbtn.primary")).toHaveCount(await page.locator(".chat-row .sm-tag-attention").count());
});
