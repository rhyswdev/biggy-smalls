import { expect, test } from "@playwright/test";
import { API } from "./helpers";

// v4 plan row 3: the overview renders from what code wrote at import and, in the second pass, from what the
// scripted writer wrote; empty fields hide their sections; the Smalls diagnosis panel follows the run from
// running to its result; contacts are code's, and the diagnosis opens them in its panel.

async function imported(request: import("@playwright/test").APIRequestContext): Promise<{ number: string; chat_id: string }> {
  const open = await (await request.get(`${API}/incidents/open`)).json();
  return (await request.post(`${API}/incidents/import`, { data: { number: open.incidents[0].number } })).json();
}

test("the overview from code: what's going on, the CI path and contacts, with the empty sections hidden", async ({ page, request }) => {
  const { number } = await imported(request);
  const body = await (await request.get(`${API}/incident/overview?number=${number}`)).json();
  test.skip(body.written_by !== "code", "the second pass writes it with the scripted writer");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What’s going on" })).toBeVisible();
  await expect(page.getByText("From the ticket and its latest work note")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Affected service and CIs" })).toBeVisible();
  await expect(page.locator(".cimap .node")).toHaveCount(body.affected_cis.length);
  for (const gone of ["Working theories from the bridge", "Timeline", "Terms you’ll hear"]) await expect(page.getByRole("heading", { name: gone })).toHaveCount(0);
  const contacts = page.getByRole("region", { name: "Contacts and resolver groups" });
  await expect(contacts.locator(".p-role").first()).toContainText("Major Incident Manager");
});

test("the Smalls diagnosis panel moves from running to its result", async ({ page, request }) => {
  const { chat_id } = await imported(request);
  await page.goto("/");
  const panel = page.getByRole("region", { name: "Smalls diagnosis" });
  await expect(panel.getByText(/In progress|Complete|Waiting on you/)).toBeVisible();
  await expect.poll(async () => (await (await request.get(`${API}/chats/${chat_id}`)).json()).chat.status, { timeout: 30_000 }).toMatch(/finished|resolved/);
  await expect(panel.getByText(/Complete/)).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByRole("link", { name: "Open diagnosis" })).toBeVisible();
});

test("@concluded the overview from the writer, with its timeline folded and the contacts in the diagnosis panel", async ({ page, request }) => {
  const { number, chat_id } = await imported(request);
  await expect.poll(async () => (await (await request.get(`${API}/incident/overview?number=${number}`)).json()).written_by, { timeout: 30_000 }).toBe("scripted");
  await page.goto("/");
  await expect(page.getByText("Written by Smalls from the ticket and its work notes")).toBeVisible();
  const timeline = page.getByRole("button", { name: /Timeline/ });
  await expect(timeline).toHaveAttribute("aria-expanded", "false");
  await timeline.click();
  await expect(page.locator(".tl li").first()).toBeVisible();
  await expect(page.getByRole("region", { name: "Smalls diagnosis" }).getByText("Probable cause", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.goto(`/chats/${chat_id}`);
  await page.getByRole("button", { name: /Contacts/ }).click();
  await expect(page.getByRole("complementary", { name: "Contacts and resolver groups" }).locator(".p-name").first()).toBeVisible();
});
