import { expect, type APIRequestContext, type Page } from "@playwright/test";

// The browser suites drive the web app; a few set the scene through the API directly, as another tab would.
export const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? (process.env.E2E_SCRIPT === "concluded" ? "7348" : "7338")}`;

export async function newChat(request: APIRequestContext, body: { origin: string; auto_approve?: boolean; trigger?: string }): Promise<string> {
  const r = await request.post(`${API}/chats`, { data: body });
  expect(r.status()).toBe(201);
  return (await r.json()).id as string;
}

export async function chatStatus(request: APIRequestContext, id: string): Promise<{ status: string; pending: number }> {
  const r = await request.get(`${API}/chats/${id}`);
  return (await r.json()).chat;
}

export async function until(request: APIRequestContext, id: string, wanted: string[]): Promise<void> {
  await expect.poll(async () => (await chatStatus(request, id)).status, { timeout: 30_000 }).toMatch(new RegExp(`^(${wanted.join("|")})$`));
}

export async function tab(page: Page, times = 1): Promise<void> {
  for (let i = 0; i < times; i++) await page.keyboard.press("Tab");
}

/** Opens everything a finished diagnosis folds away: the answer's evidence, how Smalls got here, and each round. */
export async function unfold(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");          // hydrated, so the toggles answer
  const more = page.getByRole("button", { name: "Business impact, technical detail and evidence" });
  if (await more.count()) await more.first().click();
  const how = page.getByRole("button", { name: /How Smalls got here/ });
  if (await how.count()) { await how.first().click(); await expect(how.first()).toHaveAttribute("aria-expanded", "true"); }
  const closed = page.locator(".grp > .grp-sum[aria-expanded=false]");
  for (let n = await closed.count(); n > 0; n--) await closed.first().click();
}
