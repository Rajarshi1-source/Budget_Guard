import { describe, expect, it } from "vitest";
import { burn, pct, selfShareText, unknownReason, untilText } from "../lib/format";

describe("format", () => {
  it("prints negative budgets (overspent) instead of clamping", () => {
    expect(pct(-12.46)).toBe("-12.5%");
    expect(pct(null)).toBe("—");
  });
  it("prints burn rates with one decimal", () => expect(burn(14.4)).toBe("14.4x"));
  it("relative ETA", () => {
    const now = new Date("2026-10-24T09:00:00Z");
    expect(untilText("2026-10-25T07:30:00Z", now)).toBe("in 22h 30m");
    expect(untilText("2026-10-27T13:00:00Z", now)).toBe("in 3d 4h");
    expect(untilText("2026-10-24T08:00:00Z", now)).toBe("now");
    expect(untilText(null, now)).toBe("no projection");
  });
  it("says why a service is unknown, in words", () => {
    expect(unknownReason({ state: "unknown", integrity: "no_data" })).toBe("No metrics at all");
    expect(unknownReason({ state: "healthy", integrity: "ok" })).toBeNull();
  });
  it("D-3 share: victim of a dependency outage shows a small self share", () => {
    expect(selfShareText(3, 70)).toBe("31% of spent budget is self-inflicted");
    expect(selfShareText(100, 100)).toBeNull();
  });
});
