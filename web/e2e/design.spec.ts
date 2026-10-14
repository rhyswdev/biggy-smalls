import { expect, test } from "@playwright/test";

// Row R7: every ported component renders in both themes. A screenshot of each pane is kept with the test's output.
const COMPONENTS = ["Button", "Toggle", "Handle", "StatusTag", "Confidence", "Notice", "ToolResult", "EvidenceRecord", "ConflictPair", "StakeholderMessage"];

for (const theme of ["light", "dark"] as const) {
  test(`every component renders in the ${theme} theme`, async ({ page }, info) => {
    await page.goto("/design");
    const pane = page.locator(`[data-pane="${theme}"]`);
    for (const name of COMPONENTS) {
      const box = pane.locator(`[data-component="${name}"]`);
      await expect(box, name).toBeVisible();
      expect((await box.boundingBox())?.height ?? 0, name).toBeGreaterThan(10);
    }
    const background = await pane.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(background).toBe(theme === "light" ? "rgb(245, 246, 244)" : "rgb(17, 19, 21)");
    await pane.screenshot({ path: info.outputPath(`design-${theme}.png`) });
  });
}
