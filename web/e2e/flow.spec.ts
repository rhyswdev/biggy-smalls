import { expect, test, type Locator, type Page } from "@playwright/test";

// Row R15: the whole flow with the keyboard alone, in scripted mode: from the dashboard, investigate in chat, answer
// every gate with its keys, mark it resolved, save the write-up, draft the messages and copy two of them. Nothing is
// clicked: every control is reached with Tab and used with Enter, Space or its key.

test.use({ permissions: ["clipboard-read", "clipboard-write"] });

/** Tab forward until the control has focus; fail if it cannot be reached. */
async function tabTo(page: Page, target: Locator, max = 120): Promise<void> {
  await expect(target).toBeVisible();
  for (let i = 0; i < max; i++) {
    if (await target.evaluate((el) => el === document.activeElement)) return;
    await page.keyboard.press("Tab");
  }
  throw new Error(`could not reach ${target} with Tab`);
}

test("from the dashboard to copied messages, with the keyboard alone", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await tabTo(page, page.getByRole("link", { name: "Investigate in chat" }).first());
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/chats\/new/);
  await tabTo(page, page.getByRole("button", { name: "Start investigation" }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/chats\/(?!new)[^/?]+$/);

  // Every gate takes focus on its target Approve; Shift+A answers all of it. The run asks until it reports.
  const finished = page.getByRole("button", { name: "Mark resolved…" });
  for (let round = 0; round < 12 && !(await finished.isVisible()); round++) {
    const gate = page.locator("#pending");
    await expect(gate.or(finished).first()).toBeVisible({ timeout: 30_000 });
    if (await finished.isVisible()) break;
    await expect(gate.getByRole("button", { name: /^Approve( all)?$/ }).first()).toBeFocused();
    await page.keyboard.press("Shift+A");
    await expect(gate).toHaveCount(0, { timeout: 30_000 }).catch(() => undefined);
  }
  await expect(finished).toBeVisible({ timeout: 30_000 });

  await tabTo(page, finished);
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Mark it resolved" });
  await expect(dialog).toBeVisible();
  await tabTo(page, dialog.getByLabel("How was it resolved?"));
  await page.keyboard.type("Restarted the job by hand; it finished.");
  await tabTo(page, dialog.getByRole("button", { name: "Continue to summary" }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/summary$/);

  await tabTo(page, page.getByRole("button", { name: "Save to knowledge base" }));
  await page.keyboard.press("Enter");
  await expect(page.getByText("✓ Saved to the knowledge base")).toBeVisible();
  await tabTo(page, page.getByRole("button", { name: "Draft stakeholder messages" }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/messages$/);

  await tabTo(page, page.getByRole("button", { name: "Copy message", exact: true }));
  await page.keyboard.press("Enter");
  await expect(page.getByText("1 of 4 copied")).toBeVisible();
  await tabTo(page, page.getByRole("button", { name: /^Status page/ }));
  await page.keyboard.press("Space");
  await expect(page.getByRole("textbox", { name: "Status page" })).toBeVisible();
  await tabTo(page, page.getByRole("button", { name: "Copy message", exact: true }));
  await page.keyboard.press("Enter");
  await expect(page.getByText("2 of 4 copied")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await page.getByRole("textbox", { name: "Status page" }).inputValue());
});
