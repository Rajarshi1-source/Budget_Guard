import { z } from "zod";

// The API contract, validated at the boundary. The FastAPI side owns it; keep field names identical.
export const SliState = z.enum(["healthy", "at_risk", "unknown"]);
export const Integrity = z.enum(["ok", "low_traffic", "stale", "no_data"]);
export const Verdict = z.enum(["admit", "admit_unverified", "block"]);

export const ServiceSummary = z.object({
  service: z.string(),
  state: SliState,
  integrity: Integrity,
  objective_pct: z.number(),
  remaining_pct: z.number().nullable(),          // all errors; may be negative
  remaining_self_pct: z.number().nullable(),     // D-3: self-inflicted only
  burn_rate_1h: z.number().nullable(),
  frozen: z.boolean(),
  unfreeze_eta: z.iso.datetime({ offset: true }).nullable(),
  open_incident: z.object({ id: z.number(), alertname: z.string(), started_at: z.iso.datetime({ offset: true }) }).nullable(),
  last_decision: z.object({ id: z.number(), verdict: Verdict, decided_at: z.iso.datetime({ offset: true }) }).nullable(),
  daily_remaining: z.array(z.number().nullable()).max(31),
});
export type ServiceSummary = z.infer<typeof ServiceSummary>;

export const Brief = z.object({
  text: z.string(),
  source: z.enum(["llm", "template"]),
  fallback_reason: z.string().nullable(),
});

export const Decision = z.object({
  id: z.number(),
  service: z.string(),
  sha: z.string().regex(/^[0-9a-f]{40}$/),
  verdict: Verdict,
  reason_code: z.string(),
  price_pct: z.number().nullable(),
  affordable_pct: z.number().nullable(),
  canary_profile: z.array(z.number().int().min(1).max(100)),
  policy_version: z.string(),
  inputs_hash: z.string(),
  decided_at: z.iso.datetime({ offset: true }),
  overridable: z.boolean(),                       // blocked, not yet overridden, run still re-runnable
  brief: Brief.nullable(),
  override: z.object({ approver: z.string(), reason: z.string(), created_at: z.iso.datetime({ offset: true }) }).nullable(),
  inputs: z.record(z.string(), z.unknown()),
});
export type Decision = z.infer<typeof Decision>;

export const DecisionPage = z.object({ items: z.array(Decision.omit({ inputs: true, brief: true })), next_cursor: z.string().nullable() });

export const Replay = z.object({ verdict: Verdict, reason_code: z.string(), inputs_hash: z.string(), matches: z.boolean() });
export type Replay = z.infer<typeof Replay>;

export const PolicyVersion = z.object({
  version: z.string(),
  created_at: z.iso.datetime({ offset: true }),
  active: z.boolean(),
  backtest_markdown: z.string().nullable(),
});

export const OverrideInput = z.object({
  decisionId: z.coerce.number().int().positive(),
  reason: z.string().trim().min(20, "Give at least 20 characters: what is broken and why it cannot wait").max(500),
});
