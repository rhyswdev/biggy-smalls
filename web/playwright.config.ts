import { defineConfig, devices } from "@playwright/test";

// The browser suites run the real app: the API in scripted mode over the real bundle with the generic dev
// script (no key), and the web app pointed at it. Their own ports keep them clear of a running `make app`.
const API_PORT = process.env.E2E_API_PORT ?? "7338";
const WEB_PORT = process.env.E2E_WEB_PORT ?? "7337";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } }],
  webServer: [
    {
      command: `make -C .. api SMALLS_API_MODE=scripted SMALLS_API_PORT=${API_PORT} SMALLS_API_SCRIPT_DELAY=${process.env.E2E_DELAY ?? "0.05"}`,
      url: `http://127.0.0.1:${API_PORT}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `pnpm exec next dev --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}/design`,
      env: { SMALLS_API_URL: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
