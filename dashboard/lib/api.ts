import "server-only";
import { z } from "zod";
import { Decision, DecisionPage, PolicyVersion, Replay, ServiceSummary } from "./schemas";

// BFF: the browser never talks to the platform API. The service token never leaves the server.
const BASE = process.env.BUDGETGUARD_API ?? "http://platform-api:8000";
const TOKEN = process.env.BUDGETGUARD_DASHBOARD_TOKEN ?? "";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(path: string, schema: z.ZodType<T>, init: RequestInit & { userToken?: string } = {}): Promise<T> {
  const { userToken, ...rest } = init;
  const headers = new Headers(rest.headers);
  headers.set("Authorization", `Bearer ${TOKEN}`);
  if (userToken) headers.set("X-GitHub-User-Token", userToken);   // API resolves the human itself
  if (rest.body) headers.set("Content-Type", "application/json");
  const res = await fetch(`${BASE}${path}`, { ...rest, headers, cache: "no-store", signal: AbortSignal.timeout(5_000) });
  if (!res.ok) throw new ApiError(res.status, `${rest.method ?? "GET"} ${path} -> ${res.status}`);
  return schema.parse(await res.json());
}

export const api = {
  services: () => call("/v1/services/summary", z.array(ServiceSummary)),
  decisions: (cursor?: string, service?: string) => {
    const q = new URLSearchParams();
    if (cursor) q.set("cursor", cursor);
    if (service) q.set("service", service);
    return call(`/v1/decisions/page?${q}`, DecisionPage);
  },
  decision: (id: number) => call(`/v1/decisions/${id}`, Decision),
  replay: (id: number) => call(`/v1/decisions/${id}/replay`, Replay, { method: "POST" }),
  policies: () => call("/v1/policy/versions", z.array(PolicyVersion)),
  override: (id: number, reason: string, userToken: string) =>
    call(`/v1/overrides`, z.object({ override_id: z.number() }),
      { method: "POST", body: JSON.stringify({ decision_id: id, reason }), userToken }),
};
