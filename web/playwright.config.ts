import { defineConfig, devices } from "@playwright/test";

// The browser suites run the real app: the API in scripted mode over the real bundle with the generic dev
// script (no key), and the web app pointed at it. Their own ports keep them clear of a running `make app`. The
// messages are drafted by the scripted drafter, so a draft takes the model's path through the adapter.
// E2E_SCRIPT=concluded runs the second pass (`pnpm e2e` runs both): the dev script that reaches a probable cause,
// with the scripted per-check summariser, on ports of its own, for the tests tagged @concluded (v4 plan 6.8).
const CONCLUDED = process.env.E2E_SCRIPT === "concluded";
const API_PORT = process.env.E2E_API_PORT ?? (CONCLUDED ? "7348" : "7338");
const WEB_PORT = process.env.E2E_WEB_PORT ?? (CONCLUDED ? "7347" : "7337");
const SCRIPT = CONCLUDED ? " SMALLS_API_SCRIPT=concluded SMALLS_API_OUTCOMES_PROFILE=scripted" : "";
// E2E_BASE_URL points the suites at an app that is already running, such as `docker compose up`; nothing is started.
const EXTERNAL = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "e2e",
  grep: CONCLUDED ? /@concluded/ : undefined,
  grepInvert: CONCLUDED ? undefined : /@concluded/,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: EXTERNAL ?? `http://127.0.0.1:${WEB_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } }],
  webServer: EXTERNAL ? [] : [
    {
      command: `make -C .. api SMALLS_API_MODE=scripted SMALLS_API_DRAFTER=scripted SMALLS_API_PORT=${API_PORT} SMALLS_API_SCRIPT_DELAY=${process.env.E2E_DELAY ?? "0.05"}${SCRIPT}`,
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
