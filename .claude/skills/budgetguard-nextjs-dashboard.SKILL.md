---
name: budgetguard-nextjs-dashboard
description: Build BudgetGuard's human-workflow dashboard on Next.js 16 with Node 24, React 19.2 and TypeScript strict — service cards showing HEALTHY, AT_RISK or UNKNOWN with the reason in words, all-errors and self-only budget, burn rate, a 30-day sparkline and the unfreeze ETA; the decision explorer with a recompute-from-stored-inputs button; the override flow (GitHub sign-in with Better Auth, 20-character reason, server action, API-side approver check); policy versions with their backtest reports; Freeze Brief display with source and fallback reason. Covers the BFF pattern (server-only API client, zod-validated contract), Cache Components with Suspense, async params, refresh() after actions, hydration-safe relative times, CSP headers, standalone Docker image, Vitest and Playwright. Use for ANY work under dashboard/, even if the user only says UI, cards, override page or decision viewer. MANDATE - the browser never calls the platform API, and the dashboard explains decisions but never makes one.
---

# BudgetGuard — Next.js dashboard (Rev 2 §1, §3 People plane)

Grafana shows numbers; this app hosts the three workflows Grafana cannot: **explain a
freeze, approve an override, review a policy diff**. It is deliberately small. If a feature
would change a verdict, it belongs in the platform, not here.

Pairs with: `budgetguard-admission-gate` (API endpoints and their auth),
`budgetguard-slo-math` (what UNKNOWN and negative budgets mean), `budgetguard-mlops-evals`
(brief source and backtest reports), `budgetguard-delivery-platform` (image, tunnel, CI job).

**Complete, verified source: `references/dashboard-code.md`** — `tsc --noEmit` clean,
8 Vitest tests passing, `next build` green with every page Partial-Prerendered. Copy from
there; this file holds the design and the rules.

---

## 1. Versions (resolved 24 Oct 2026)

| Package | Pin | Note |
| --- | --- | --- |
| Node | 24 LTS | Portfolio baseline (Next 16 needs ≥ 20.9) |
| next | 16.3.8 | Rev 2 says 16.3.x; **16.4.0 shipped 6 Oct and 16.4.3 on 23 Oct**. Stay on 16.3.8 for the build, try 16.4 on a branch |
| react / react-dom | 19.2.0 | |
| zod | 4.x | `z.iso.datetime()` is the v4 spelling |
| better-auth | 1.7.x | Auth.js is now maintained by the Better Auth team, which recommends Better Auth for new projects |
| vitest / @playwright/test | 4.x / 1.5x | |

---

## 2. Architecture

```text
Browser ──► Next.js (server components, server actions) ──► platform-api /v1/...
   ▲            │  BUDGETGUARD_DASHBOARD_TOKEN (server-only)      │
   │            │  + X-GitHub-User-Token for overrides only       │ resolves login via GET /user,
   └── HTML ◄───┘                                                 │ checks approvers, writes override
```

| Decision | Choice | Why NOT the alternative |
| --- | --- | --- |
| Data access | BFF: `lib/api.ts` with `import "server-only"` | Browser→API needs CORS, exposes the API publicly and leaks a token |
| Contract | zod schemas at the boundary | Generated types from OpenAPI give compile-time safety only; zod also rejects a bad payload at runtime (a new `state` value is an error, not a green card) |
| Live updates | `router.refresh()` every 15 s while visible | Snapshots change every 60 s; websockets add a server and reconnection logic for nothing |
| Caching | `cacheComponents: true`, no `"use cache"` on live data | Next 16 caching is opt-in; reliability numbers must never be served stale from a page cache. The API's 15 s Valkey cache is the only cache |
| Auth | Better Auth + GitHub, scope `read:user`, sessions in PostgreSQL schema `auth` | Clerk is SaaS; Auth.js is in maintenance; hand-rolled OAuth is a security liability |
| Authorization | In the **API**, from the user's GitHub token | A dashboard that asserts "approver = alice" lets a compromised dashboard approve as anyone |
| Charts | One hand-written SVG sparkline | A chart library for one line is 100 KB of JavaScript |
| Middleware / `proxy.ts` | None | Auth checks sit next to the data (server action + API); `proxy.ts` would only be an optimistic redirect |

---

## 3. Pages

| Route | Content | Rendering |
| --- | --- | --- |
| `/` | One card per service | Static shell; cards stream inside `<Suspense>` |
| `/decisions` | Paged table: time, service, commit, verdict (+ overridden), reason, price / affordable | `searchParams` awaited inside the Suspense child |
| `/decisions/[id]` | Verdict, numbers, canary, policy, inputs hash, Freeze Brief with source, override record, **Recompute**, override form when allowed, stored inputs | `params` awaited inside the Suspense child |
| `/policy` | Policy versions, active marker, the backtest report stored with each version | |
| `/api/auth/[...all]` | Better Auth handler | Dynamic |

Override form appears only when the API says `overridable` (blocked, not yet overridden,
run young enough to re-run). The API re-checks everything; the flag only hides the form.
An override never "approves the waiting run": GitHub fails the job as soon as the gate
rejects it. The API records a single-use, 30-minute override and **re-runs the failed job**;
the fresh evaluation admits it on the override lane with a 5% canary
(`budgetguard-admission-gate`, fourth flag).

---

## 4. Rules that came from real failures

1. **Next 16 async APIs.** `params`, `searchParams`, `headers()`, `cookies()` are promises.
   With `cacheComponents`, reading them or fetching uncached data **outside** `<Suspense>`
   fails the build. Pattern: the page component is synchronous and renders
   `<Suspense><Inner params={params} /></Suspense>`; `Inner` awaits.
2. **`refresh()` after a server action** (from `next/cache`, Next 16) re-reads uncached data
   on the page. `revalidateTag` now needs a `cacheLife` profile and is for cached data only.
3. **Better Auth 1.7 `getAccessToken` takes the local account row id**, not
   `providerId`. List the user's accounts, pick `providerId === "github"`, pass its `id`.
   (TypeScript caught this; the docs examples many people copy are older.)
4. **Hydration-safe time.** The server renders the absolute UTC time; the client adds
   "in 22h 30m" after mount. Rendering `Date.now()` on both sides produces mismatches.
5. **UNKNOWN is a sentence, not a colour.** Every state has a shape and a label, and UNKNOWN
   says why ("No metrics at all", "Scrape or probe failing").
6. **Negative budgets print as negative.** `-12.5%` is information (overspent); clamping
   to 0 hides how long the freeze will last.
7. **Show the D-3 split.** "31% of spent budget is self-inflicted" is the one line that
   explains why a victim service is not frozen.
8. **Label demo data on the page.** The public URL runs synthetic traffic.

---

## 5. Key excerpts

The server-only client (all calls go through it):

```ts
async function call<T>(path: string, schema: z.ZodType<T>, init: RequestInit & { userToken?: string } = {}) {
  const { userToken, ...rest } = init;
  const headers = new Headers(rest.headers);
  headers.set("Authorization", `Bearer ${TOKEN}`);
  if (userToken) headers.set("X-GitHub-User-Token", userToken);   // API resolves the human itself
  const res = await fetch(`${BASE}${path}`, { ...rest, headers, cache: "no-store", signal: AbortSignal.timeout(5_000) });
  if (!res.ok) throw new ApiError(res.status, `${rest.method ?? "GET"} ${path} -> ${res.status}`);
  return schema.parse(await res.json());
}
```

The override action (session → validation → token → API → `refresh()`):

```ts
const session = await auth.api.getSession({ headers: h });
if (!session) return { error: "Sign in with GitHub first" };
const parsed = OverrideInput.safeParse({ decisionId: form.get("decisionId"), reason: form.get("reason") });
if (!parsed.success) return { error: parsed.error.issues[0]?.message ?? "Invalid input" };
const github = (await auth.api.listUserAccounts({ headers: h })).find((a) => a.providerId === "github");
const { accessToken } = await auth.api.getAccessToken({ body: { accountId: github!.id }, headers: h });
await api.override(parsed.data.decisionId, parsed.data.reason, accessToken);   // 403 / 409 mapped to messages
refresh();
```

Error mapping the user sees: 403 → "Your GitHub login is not on the approvers list";
409 → "Nothing to override: not blocked, already overridden, or too old to re-run";
anything else → "Override failed; nothing was approved". On success the copy says the job
is being re-run, never that it was approved.

---

## 6. Tests

| Layer | What | Tool |
| --- | --- | --- |
| Pure functions | `pct` (negatives), `burn`, `untilText`, `unknownReason`, `selfShareText` | Vitest |
| Contract | UNKNOWN card with nulls parses; unknown `state` rejected; 20-char reason | Vitest + zod |
| Flows | unknown card explains itself; recompute says "Same verdict"; short reason shows an alert; "Override and re-run" only on overridable blocks | Playwright against a fake API built from the platform's OpenAPI examples |
| Build | `next build` in CI must stay all-◐ (no accidental fully dynamic page) | CI step |

---

## 7. Interview soundbites

- "The dashboard explains decisions; it never makes them. If it is down, deploys are
  still gated."
- "Every decision page has a Recompute button: the API replays the stored inputs through
  the same gate code and shows that the hash and verdict match."
- "Override authorization lives in the API. The dashboard forwards the user's GitHub token
  and the API asks GitHub who that is, so a compromised dashboard can't approve as someone else."
- "Next 16 made caching opt-in, which is what reliability data wants: the page shell is
  prerendered, the numbers stream in fresh."
- "UNKNOWN is never a grey dot. It says 'no metrics at all' or 'scrape failing', because the
  whole point of D-4 is that a dead service must not look healthy."
