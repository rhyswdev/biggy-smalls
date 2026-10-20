import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { API, newChat, unfold, until } from "./helpers";

// Row R15: axe finds nothing on any page, in either theme. Each theme's test walks every screen and state the app
// has (dashboard, design system, new chat, a chat waiting on reads, a finished chat, the drawer, the resolve dialog,
// a recorded run, the summary, the messages and the knowledge-base entry, and the not-found page) and reports every
// violation together, so one run shows all of them.

test.use({ permissions: ["clipboard-read", "clipboard-write"] });

for (const theme of ["light", "dark"] as const) {
  test(`axe finds nothing on any page in the ${theme} theme`, async ({ page, request, baseURL }) => {
    test.setTimeout(180_000);
    await page.context().addCookies([{ name: "smalls-theme", value: theme, url: baseURL! }]);
    const found: string[] = [];
    async function scan(label: string) {
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const { violations } = await new AxeBuilder({ page }).analyze();
      for (const v of violations) found.push(`${label}: ${v.id} (${v.impact}) × ${v.nodes.length}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
    }
    async function visit(path: string, label: string, ready: (p: Page) => Promise<void>) {
      await page.goto(path);
      await ready(page);
      await scan(label);
    }

    const waiting = await newChat(request, { origin: "chat", auto_approve: false });
    const done = await newChat(request, { origin: "headless" });
    const resolved = await newChat(request, { origin: "headless" });
    await until(request, waiting, ["waiting"]);
    await until(request, done, ["finished"]);
    await until(request, resolved, ["finished"]);
    await request.put(`${API}/chats/${resolved}/resolution`, { data: { path: "own", note: "Restarted the job by hand.", resolved_at: "02:40", resolved_by: "Commander" } });
    const past = ((await (await request.get(`${API}/chats`)).json()) as { id: string; origin: string }[]).find((c) => c.origin === "past");

    await visit("/", "first run or overview", async (p) => { await expect(p.locator("main")).toBeVisible(); });
    await visit("/design", "design system", async (p) => { await expect(p.locator("main")).toBeVisible(); });
    await visit("/chats/new", "new chat", async (p) => { await expect(p.getByRole("textbox").first()).toBeVisible(); });
    await visit(`/chats/${waiting}`, "chat waiting on reads", async (p) => { await expect(p.locator("#pending")).toBeVisible(); });
    // The gate's two forms, opened with their keys on the read in focus: the edit form, then the reject reason.
    const target = page.locator("#pending [data-kf] button").first();
    await expect(target).toBeFocused();
    await page.keyboard.press("e");
    await expect(page.getByRole("button", { name: "Approve with edit" })).toBeVisible();
    await scan("gate, edit form");
    await page.keyboard.press("Escape");
    await expect(target).toBeFocused();                                 // keys act on the read in focus, once focus is back
    await page.keyboard.press("r");
    await expect(page.getByLabel("Reason for rejecting")).toBeFocused();
    await scan("gate, reject reason");
    await visit(`/chats/${done}`, "finished chat", async (p) => { await expect(p.locator(".lead")).toBeVisible(); });
    await unfold(page);
    await scan("finished chat, unfolded");
    await page.getByRole("button", { name: /Working picture/ }).click();
    await scan("working picture panel");
    const handle = await page.locator("button.sm-handle").first().getAttribute("data-handle");
    await visit(`/chats/${done}?evidence=${encodeURIComponent(handle!)}`, "evidence drawer", async (p) => { await expect(p.getByRole("dialog", { name: `Evidence ${handle}` })).toBeVisible(); });
    await visit(`/chats/${done}?resolve`, "resolve dialog", async (p) => { await expect(p.getByRole("dialog", { name: "Resolve incident" })).toBeVisible(); });
    if (past) await visit(`/chats/${past.id}`, "recorded run", async (p) => { await expect(p.locator(".lead")).toBeVisible(); });
    await visit(`/chats/${resolved}/resolution`, "summary", async (p) => { await expect(p.getByRole("button", { name: "Save as knowledge article" })).toBeVisible(); });
    await page.getByRole("button", { name: "Save as knowledge article" }).click();
    await page.getByRole("button", { name: "Draft communications →" }).click();
    await expect(page).toHaveURL(/\/messages$/);
    await expect(page.getByRole("textbox", { name: "Executives" })).toBeVisible();
    await scan("messages");
    await page.getByRole("button", { name: /Knowledge base entry/ }).click();
    await expect(page.getByRole("article", { name: "The knowledge base entry" })).toBeVisible();
    await scan("knowledge base entry");
    await visit("/chats/no-such-chat", "not found", async (p) => { await expect(p.locator("main")).toBeVisible(); });

    expect(found, found.join("\n")).toEqual([]);
  });
}
