// A stand-in for the platform API, serving fixed fixtures shaped like the real contract
// (lib/schemas.ts parses every response, so a drifted fixture fails the flow, not silently).
// Playwright starts it beside `next start`; nothing here is reachable from a browser.
import { createServer } from "node:http";

const PORT = Number(process.env.FAKE_API_PORT ?? 4010);
const TOKEN = process.env.BUDGETGUARD_DASHBOARD_TOKEN ?? "e2e-dashboard-token";
const NOW = "2026-10-31T09:00:00+00:00";
const SHA = "3f9c1e0a7b2d4c6e8f0a1b2c3d4e5f6a7b8c9d0e";
const HASH = "9a1c2f3e4d5b6a7980f1e2d3c4b5a69788f9e0d1c2b3a4958677e8f9a0b1c2d3";

const service = (name, extra) => ({
  service: name, state: "healthy", integrity: "ok", objective_pct: 99.9,
  remaining_pct: 72.4, remaining_self_pct: 90.1, burn_rate_1h: 0.4, frozen: false,
  unfreeze_eta: null, open_incident: null, last_decision: null,
  daily_remaining: Array.from({ length: 30 }, (_, i) => 80 - i * 0.25), ...extra,
});

const SERVICES = [
  service("auth-service", {
    state: "at_risk", objective_pct: 99.95, remaining_pct: 3.1, remaining_self_pct: 3.4, burn_rate_1h: 16.2,
    frozen: true, unfreeze_eta: "2026-11-01T14:20:00+00:00",
    open_incident: { id: 41, alertname: "ErrorBudgetBurnPage", started_at: "2026-10-31T08:12:00+00:00" },
    last_decision: { id: 4812, verdict: "block", decided_at: NOW },
  }),
  service("orders-service", { last_decision: { id: 4813, verdict: "admit", decided_at: NOW } }),
  service("inventory-service", {
    state: "unknown", integrity: "no_data", objective_pct: 99.5, remaining_pct: null,
    remaining_self_pct: null, burn_rate_1h: null, daily_remaining: [],
  }),
  service("notifications-service", { objective_pct: 99.0 }),
];

const decision = (id, extra) => ({
  id, service: "auth-service", sha: SHA, verdict: "block", reason_code: "ACTIVE_PAGE",
  price_pct: null, affordable_pct: null, canary_profile: [], policy_version: "c0ffee1d2e3f",
  inputs_hash: HASH, decided_at: NOW, overridable: true, override: null,
  brief: { text: "auth-service is paging: a self-inflicted burn is firing. Deploys wait until it clears.",
           source: "template", fallback_reason: "no_api_key" },
  inputs: { gate_input: { service: "auth-service", page_firing: true } }, ...extra,
});

const DECISIONS = {
  4812: decision(4812),
  4813: decision(4813, {
    service: "orders-service", verdict: "admit", reason_code: "WITHIN_BUDGET", price_pct: 0.42,
    affordable_pct: 33.7, canary_profile: [25, 100], overridable: false, brief: null,
  }),
};

const strip = (d) => Object.fromEntries(Object.entries(d).filter(([k]) => k !== "inputs" && k !== "brief"));
const ROUTES = {
  "GET /v1/services/summary": () => SERVICES,
  "GET /v1/decisions/page": () => ({ items: Object.values(DECISIONS).map(strip), next_cursor: null }),
  "GET /v1/policy/versions": () => [{ version: "c0ffee1d2e3f", created_at: NOW, active: true, backtest_markdown: null }],
};

createServer((req, res) => {
  const send = (status, body) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  };
  if (req.headers.authorization !== `Bearer ${TOKEN}`) return send(401, { detail: "unauthorized" });
  const path = new URL(req.url, "http://x").pathname;
  const route = ROUTES[`${req.method} ${path}`];
  if (route) return send(200, route());
  const m = path.match(/^\/v1\/decisions\/(\d+)(\/replay)?$/);
  const d = m && DECISIONS[m[1]];
  if (!d) return send(404, { detail: "not found" });
  if (m[2] && req.method === "POST") {
    return send(200, { verdict: d.verdict, reason_code: d.reason_code, inputs_hash: d.inputs_hash, matches: true });
  }
  if (req.method === "GET") return send(200, d);
  send(405, { detail: "method not allowed" });
}).listen(PORT, "127.0.0.1", () => console.log(`fake platform API on :${PORT}`));
