import { expect, test } from "@playwright/test";
import { API, newChat, unfold, until } from "./helpers";

// Row R11: every handle in a scripted chat opens its record in the drawer; Esc closes it and focus returns to the
// handle; nothing shown is at or after `now`; a handle that does not resolve shows the error notice.

const ISO = /\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z)\b/g;

test("every handle in a finished chat opens its record, and none shows a time after now", async ({ page, request }) => {
  const now = Date.parse((await (await request.get(`${API}/capabilities`)).json()).now);
  const id = await newChat(request, { origin: "headless" });
  await until(request, id, ["finished"]);
  await page.goto(`/chats/${id}`);
  await unfold(page);          // the evidence and the receipts are one click away
  const handles = [...new Set(await page.locator("button.sm-handle[data-handle]").evaluateAll((els) => els.map((e) => e.getAttribute("data-handle")!)))];
  expect(handles.length).toBeGreaterThan(2);
  for (const handle of handles) {
    const button = page.locator(`button.sm-handle[data-handle="${handle}"]`).first();
    await button.click();
    const drawer = page.getByRole("dialog", { name: `Evidence ${handle}` });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText("Cited by")).toBeVisible();
    await expect(drawer.locator(".sm-notice-error")).toHaveCount(0);
    expect((await drawer.boundingBox())!.width).toBeLessThanOrEqual(560);
    // A known time carries its day, so a record from the day before never reads as later than now. A computed
    // result has none.
    await expect(drawer.locator(".time").first()).toHaveText(/^(known \d{1,2} [A-Z][a-z]{2} \d\d:\d\d(:\d\d)? UTC)?$/);
    const text = (await drawer.innerText()).replace(/planned \S+/g, "");
    for (const m of text.matchAll(ISO)) expect(Date.parse(m[1]!), `${handle}: ${m[1]}`).toBeLessThanOrEqual(now);
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(button).toBeFocused();
  }
});

test("a handle that does not resolve shows the error notice", async ({ page, request }) => {
  const id = await newChat(request, { origin: "headless" });
  await until(request, id, ["finished"]);
  await page.goto(`/chats/${id}?evidence=${encodeURIComponent("snow:INC9999999")}`);
  const drawer = page.getByRole("dialog", { name: "Evidence snow:INC9999999" });
  await expect(drawer.locator(".sm-notice-error")).toContainText("not in this chat or in the data at its now");
  await drawer.getByRole("button", { name: "Close evidence drawer" }).click();
  await expect(drawer).toBeHidden();
});

test("a related record opens in the drawer with a way back", async ({ page, request }) => {
  const id = await newChat(request, { origin: "headless" });
  await until(request, id, ["finished"]);
  await page.goto(`/chats/${id}`);
  await unfold(page);          // the evidence and the receipts are one click away
  const handles = await page.locator("button.sm-handle[data-handle]").evaluateAll((els) => els.map((e) => e.getAttribute("data-handle")!));
  for (const handle of handles) {
    await page.locator(`button.sm-handle[data-handle="${handle}"]`).first().click();
    const drawer = page.getByRole("dialog", { name: `Evidence ${handle}` });
    await expect(drawer.getByText("Related records")).toBeVisible();
    const related = drawer.locator("section", { hasText: "Related records" }).locator("button.sm-handle");
    if (await related.count()) {
      const next = await related.first().getAttribute("data-handle");
      await related.first().click();
      await expect(page.getByRole("dialog", { name: `Evidence ${next}` })).toBeVisible();
      await page.getByRole("button", { name: `← ${handle}` }).click();
      await expect(page.getByRole("dialog", { name: `Evidence ${handle}` })).toBeVisible();
      return;
    }
    await page.keyboard.press("Escape");
  }
  test.skip(true, "no handle in this run has a related record");
});
