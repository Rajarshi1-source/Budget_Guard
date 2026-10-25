import { describe, expect, it } from "vitest";
import { OverrideInput, ServiceSummary } from "../lib/schemas";

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
