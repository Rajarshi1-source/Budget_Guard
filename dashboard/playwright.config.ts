import { defineConfig, devices } from "@playwright/test";

// The flows run against the production build (`npm run build` first) and a fake platform API,
// so they need no GitHub, platform or cluster. Better Auth still validates its schema on first
// use, so a throwaway PostgreSQL with platform/sql/006_auth.sql loaded must listen on 5434
// (ci.yml starts one). No session cookie is ever set.
const env = {
  BUDGETGUARD_API: "http://127.0.0.1:4010",
  BUDGETGUARD_DASHBOARD_TOKEN: "e2e-dashboard-token",
  BETTER_AUTH_URL: "http://127.0.0.1:3100",
  BETTER_AUTH_SECRET: "e2e-only-placeholder-value-not-a-secret",
  AUTH_DATABASE_URL: process.env.E2E_AUTH_DATABASE_URL ?? "postgres://postgres:e2e@127.0.0.1:5434/postgres",
  NEXT_TELEMETRY_DISABLED: "1",
};

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: "http://127.0.0.1:3100", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: "node e2e/fake-api.mjs", url: "http://127.0.0.1:4010/livez", env,   // 401 counts as up
      reuseExistingServer: !process.env.CI },
    { command: "npx next start -H 127.0.0.1 -p 3100", url: "http://127.0.0.1:3100/policy", env,
      reuseExistingServer: !process.env.CI, timeout: 60_000 },
  ],
});
