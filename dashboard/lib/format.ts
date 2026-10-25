// Pure formatting helpers: unit-tested, shared by server and client components.
import type { ServiceSummary } from "./schemas";

export function pct(x: number | null, digits = 1): string {
  if (x === null || Number.isNaN(x)) return "—";
  return `${x.toFixed(digits)}%`;
}

export function burn(x: number | null): string {
  return x === null ? "—" : `${x.toFixed(1)}x`;
}

/** "in 22h 30m" / "in 3d 4h" / "now" — relative to `now`, which the caller passes for testability. */
export function untilText(iso: string | null, now: Date): string {
  if (!iso) return "no projection";
  const ms = new Date(iso).getTime() - now.getTime();
  if (ms <= 0) return "now";
  const mins = Math.round(ms / 60_000);
  const d = Math.floor(mins / 1440), h = Math.floor((mins % 1440) / 60), m = mins % 60;
  if (d > 0) return `in ${d}d ${h}h`;
  if (h > 0) return `in ${h}h ${m}m`;
  return `in ${m}m`;
}

export const STATE_LABEL: Record<ServiceSummary["state"], string> = {
  healthy: "Healthy",
  at_risk: "At risk",
  unknown: "Unknown — no trustworthy signal",
};

/** Why a card says UNKNOWN: say it in words, never only with a colour. */
export function unknownReason(s: Pick<ServiceSummary, "state" | "integrity">): string | null {
  if (s.state !== "unknown") return null;
  return { no_data: "No metrics at all", stale: "Scrape or probe failing", low_traffic: "Too little traffic", ok: "Budget unavailable" }[s.integrity];
}

/** The D-3 story on one line: how much of the burn is the service's own. */
export function selfShareText(all: number | null, self: number | null): string | null {
  if (all === null || self === null) return null;
  const spentAll = 100 - all, spentSelf = 100 - self;
  if (spentAll <= 0) return null;
  const share = Math.max(0, Math.min(100, (spentSelf / spentAll) * 100));
  return `${share.toFixed(0)}% of spent budget is self-inflicted`;
}
