import { describe, expect, it } from "vitest";
import { Decision, OverrideInput, Replay, ServiceSummary } from "../lib/schemas";

const card = {
  service: "orders-service", state: "unknown", integrity: "no_data", objective_pct: 99.9,
  remaining_pct: null, remaining_self_pct: null, burn_rate_1h: null, frozen: false,
  unfreeze_eta: null, open_incident: null, last_decision: null, daily_remaining: [null, 40.2],
};

describe("API contract", () => {
  it("accepts an UNKNOWN card with null numbers", () => expect(ServiceSummary.parse(card).state).toBe("unknown"));
  it("rejects an unknown state value instead of rendering it", () =>
    expect(() => ServiceSummary.parse({ ...card, state: "green" })).toThrow());
  it("requires a 20-character override reason", () => {
    expect(OverrideInput.safeParse({ decisionId: "7", reason: "urgent" }).success).toBe(false);
    expect(OverrideInput.safeParse({ decisionId: "7", reason: "payment webhook broken; fix needs this deploy" }).success).toBe(true);
  });
});

// Shapes the platform API returns since week 5 (api.py _decision_json, decision_replay).
const decision = {
  id: 4814, service: "orders-service", sha: "9f3c2ab".padEnd(40, "0"), verdict: "block",
  reason_code: "ACTIVE_PAGE", price_pct: null, affordable_pct: null, canary_profile: [25, 100],
  policy_version: "p".repeat(40), inputs_hash: "h".repeat(64), decided_at: "2026-10-31T12:00:00+00:00",
  overridable: true, override: null, inputs: { facts: {}, gate_input: {}, attribution: "charge_origin" },
  brief: { text: "BudgetGuard: Blocked (ACTIVE_PAGE) for orders-service.", source: "template", fallback_reason: null },
};

describe("week 5 contract", () => {
  it("accepts a blocked decision with its template brief", () =>
    expect(Decision.parse(decision).brief?.source).toBe("template"));
  it("accepts an admin-bypass record (no canary, no brief)", () =>
    expect(Decision.parse({ ...decision, reason_code: "ADMIN_BYPASS", verdict: "admit", canary_profile: [], brief: null,
                            overridable: false }).canary_profile).toEqual([]));
  it("accepts a replay result", () =>
    expect(Replay.parse({ verdict: "block", reason_code: "ACTIVE_PAGE", inputs_hash: "h".repeat(64), matches: true }).matches)
      .toBe(true));
});
