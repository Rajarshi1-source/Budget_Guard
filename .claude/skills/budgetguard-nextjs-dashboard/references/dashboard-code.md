# BudgetGuard dashboard — complete source

Next.js 16.3.8, React 19.2, TypeScript strict, zod 4, Better Auth 1.7. Verified in the
skill-authoring session on 24 Oct 2026: `tsc --noEmit` clean, 8 Vitest tests pass, and
`next build` succeeds with every page Partial-Prerendered (static shell + streamed live data).
The Playwright spec typechecks but was not executed (it needs the fake API in CI).
Re-run 25 Oct 2026 on Node 24.12 from this file's own code: `tsc` clean, 8 Vitest pass,
`next build` all Partial Prerender, and `npm run lint` clean after two fixes made here
(ESLint 9 + `eslint.config.mjs` were missing; `RelativeTime` set state inside an effect, now
`useSyncExternalStore` with a `null` server snapshot). Better Auth logs "Could not validate the
database schema" during `next build` without a database; that is expected and the build passes.

Build output:

```text
Route (app)
┌ ◐ /
├ ◐ /_not-found
├ ƒ /api/auth/[...all]
├ ◐ /decisions
├   /decisions/[id]
│ └ ◐ /decisions/[id]
└ ◐ /policy

◐  (Partial Prerender)  prerendered as static HTML with dynamic server-streamed content
```

Environment: `BUDGETGUARD_API`, `BUDGETGUARD_DASHBOARD_TOKEN`, `AUTH_DATABASE_URL`,
`BETTER_AUTH_URL`, `BETTER_AUTH_SECRET`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`.
Set `BETTER_AUTH_SECRET` for `next build` too (Better Auth logs an error at build time
without it). Better Auth's tables (schema `auth`): `@better-auth/cli` stopped at 1.4.x; from 1.5 the CLI is
the `auth` package. The repo does not run either: `scripts/auth-schema.mjs` compiles the SQL with
the installed better-auth's own planner (`getMigrations` from `better-auth/db/migration`) and
Alembic migration `006` applies it as `bg_auth` (W4, 26 Oct 2026). The standalone image has no
CLI, and one tool owns every schema. Regenerate after a better-auth upgrade.

## Files

### `package.json`

```json
{
  "name": "budgetguard-dashboard",
  "private": true,
  "type": "module",
  "engines": {
    "node": ">=24"
  },
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "e2e": "playwright test"
  },
  "dependencies": {
    "better-auth": "1.7.12",
    "next": "16.3.8",
    "pg": "8.16.3",
    "react": "19.2.0",
    "react-dom": "19.2.0",
    "server-only": "0.0.1",
    "zod": "4.1.12"
  },
  "devDependencies": {
    "@playwright/test": "1.56.1",
    "@types/node": "24.9.1",
    "@types/pg": "8.15.5",
    "@types/react": "19.2.2",
    "@types/react-dom": "19.2.2",
    "eslint": "9.39.1",
    "eslint-config-next": "16.3.8",
    "typescript": "5.9.3",
    "vitest": "4.0.5"
  }
}
```

### `next.config.ts`

```ts
import type { NextConfig } from "next";

const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",     // Next inline bootstrap; tighten with nonces via proxy.ts if needed
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: https://avatars.githubusercontent.com",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self' https://github.com",
].join("; ");

const config: NextConfig = {
  output: "standalone",          // small runtime image: node server.js
  cacheComponents: true,         // Next 16: caching is opt-in; live data must sit inside <Suspense>
  poweredByHeader: false,
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "Content-Security-Policy", value: csp },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      ],
    }];
  },
};

export default config;
```

### `eslint.config.mjs`

Next 16 removed `next lint`; `npm run lint` (run by `ci.yml`) needs ESLint 9 and this flat
config. Without them the CI dashboard job fails on its first run (found 25 Oct 2026).

```js
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

// Next 16 removed `next lint`; ESLint 9 flat config is the supported setup.
export default defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),
]);
```

### `tsconfig.json`

```json
{
  "compilerOptions": {
    "target": "ES2023",
    "lib": [
      "dom",
      "dom.iterable",
      "ES2023"
    ],
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noEmit": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "react-jsx",
    "skipLibCheck": true,
    "incremental": true,
    "plugins": [
      {
        "name": "next"
      }
    ],
    "paths": {
      "@/*": [
        "./*"
      ]
    },
    "allowJs": true,
    "esModuleInterop": true
  },
  "include": [
    "next-env.d.ts",
    "**/*.ts",
    "**/*.tsx",
    ".next/types/**/*.ts",
    ".next/dev/types/**/*.ts"
  ],
  "exclude": [
    "node_modules"
  ]
}
```

### `lib/schemas.ts`

```ts
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
```

### `lib/api.ts`

```ts
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
```

### `lib/format.ts`

```ts
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
```

### `lib/auth.ts`

```ts
import "server-only";
import { betterAuth } from "better-auth";
import { nextCookies } from "better-auth/next-js";
import { Pool } from "pg";

// Better Auth (Auth.js is now maintained by the Better Auth team; new projects start here).
// Sessions live in PostgreSQL, schema "auth", separate from BudgetGuard's tables.
export const auth = betterAuth({
  baseURL: process.env.BETTER_AUTH_URL,          // e.g. https://budgetguard.example.dev
  secret: process.env.BETTER_AUTH_SECRET,        // 32+ random bytes; rotate like any signing key
  database: new Pool({ connectionString: process.env.AUTH_DATABASE_URL, options: "-c search_path=auth" }),
  socialProviders: {
    github: {
      clientId: process.env.GITHUB_CLIENT_ID ?? "",
      clientSecret: process.env.GITHUB_CLIENT_SECRET ?? "",
      scope: ["read:user"],          // identity only; authorization is decided by the platform API
    },
  },
  plugins: [nextCookies()],          // lets server actions set cookies
});
```

### `lib/auth-client.ts`

```ts
"use client";
import { createAuthClient } from "better-auth/react";

export const authClient = createAuthClient();
```

### `app/api/auth/[...all]/route.ts`

```ts
import { toNextJsHandler } from "better-auth/next-js";
import { auth } from "@/lib/auth";

export const { GET, POST } = toNextJsHandler(auth);
```

### `app/layout.tsx`

```tsx
import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { SessionBadge } from "@/components/SessionBadge";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "BudgetGuard", template: "%s · BudgetGuard" },
  description: "Error-budget admission control: freeze explanations, decision records, overrides and policy backtests.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="top">
          <Link href="/" className="brand">BudgetGuard</Link>
          <nav aria-label="Main">
            <Link href="/">Services</Link>
            <Link href="/decisions">Decisions</Link>
            <Link href="/policy">Policy</Link>
          </nav>
          <Suspense fallback={null}><SessionBadge /></Suspense>
        </header>
        {children}
      </body>
    </html>
  );
}
```

### `app/globals.css`

```css
:root { --bg:#fbfaf7; --fg:#1d1d1b; --muted:#6b6b66; --line:#e3e1da; --ok:#1f7a4d; --risk:#a35b00; --unk:#5b5bd6; --bad:#b42318; }
@media (prefers-color-scheme: dark) { :root { --bg:#141413; --fg:#ecebe6; --muted:#a3a29b; --line:#2c2b28; --ok:#4cc38a; --risk:#f5a524; --unk:#9b9bf5; --bad:#f97066; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
.top { display:flex; gap:1.5rem; align-items:center; padding:.75rem 1rem; border-bottom:1px solid var(--line); }
.top nav { display:flex; gap:1rem; flex:1; }
a { color:inherit; }
main { padding:1rem; max-width:1100px; margin:0 auto; }
.cards { display:grid; gap:1rem; grid-template-columns:repeat(auto-fill, minmax(250px, 1fr)); }
.card { border:1px solid var(--line); border-radius:10px; padding:1rem; }
.badge { display:inline-flex; gap:.35rem; align-items:center; font-weight:600; }
.badge[data-state=healthy]{ color:var(--ok);} .badge[data-state=at_risk]{ color:var(--risk);} .badge[data-state=unknown]{ color:var(--unk);}
.verdict[data-v=block]{ color:var(--bad);} .verdict[data-v=admit]{ color:var(--ok);} .verdict[data-v=admit_unverified]{ color:var(--risk);}
.muted { color:var(--muted); } .num { font-variant-numeric: tabular-nums; }
pre { overflow-x:auto; background:color-mix(in srgb, var(--fg) 5%, transparent); padding:.75rem; border-radius:8px; }
table { border-collapse:collapse; width:100%; } td,th { text-align:left; padding:.35rem .5rem; border-bottom:1px solid var(--line); }
.demo-note { font-size:.85rem; color:var(--muted); }
```

### `app/page.tsx`

```tsx
import { Suspense } from "react";
import { api } from "@/lib/api";
import { AutoRefresh } from "@/components/AutoRefresh";
import { ServiceCard } from "@/components/ServiceCard";

export default function Home() {
  return (
    <main>
      <h1>Service reliability</h1>
      <p className="demo-note">Demo environment: traffic is synthetic and faults are injected on a schedule.</p>
      <Suspense fallback={<p className="muted">Loading services…</p>}>
        <ServiceCards />
      </Suspense>
      <AutoRefresh seconds={15} />
    </main>
  );
}

async function ServiceCards() {
  const services = await api.services();
  return <section className="cards">{services.map((s) => <ServiceCard key={s.service} s={s} />)}</section>;
}
```

### `app/error.tsx`

```tsx
"use client";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main>
      <h1>BudgetGuard API unavailable</h1>
      <p className="muted">The gate keeps deciding without the dashboard; this page only explains decisions. ({error.digest ?? "no digest"})</p>
      <button type="button" onClick={reset}>Try again</button>
    </main>
  );
}
```

### `components/ServiceCard.tsx`

```tsx
import Link from "next/link";
import type { ServiceSummary } from "@/lib/schemas";
import { burn, pct, selfShareText, STATE_LABEL, unknownReason } from "@/lib/format";
import { RelativeTime } from "./RelativeTime";
import { Sparkline } from "./Sparkline";

const ICON = { healthy: "●", at_risk: "▲", unknown: "?" } as const;   // shape + text, never colour alone

export function ServiceCard({ s }: { s: ServiceSummary }) {
  const why = unknownReason(s);
  const share = selfShareText(s.remaining_pct, s.remaining_self_pct);
  return (
    <article className="card" aria-labelledby={`svc-${s.service}`}>
      <h2 id={`svc-${s.service}`}>{s.service}</h2>
      <p className="badge" data-state={s.state}><span aria-hidden>{ICON[s.state]}</span>{STATE_LABEL[s.state]}</p>
      {why && <p className="muted">{why}. Deploys follow this service&apos;s declared failure mode.</p>}
      <dl className="num">
        <dt>Budget remaining (30 d, SLO {s.objective_pct}%)</dt><dd>{pct(s.remaining_pct)}</dd>
        <dt>Burn rate (1 h)</dt><dd>{burn(s.burn_rate_1h)}</dd>
      </dl>
      {share && <p className="muted">{share}</p>}
      <Sparkline values={s.daily_remaining} />
      {s.frozen && (
        <p><strong>Frozen.</strong>{" "}
          {s.unfreeze_eta ? <>Unfreezes at <RelativeTime iso={s.unfreeze_eta} /> if traffic holds.</> : "No unfreeze projection."}
        </p>
      )}
      {s.open_incident && <p>Open: {s.open_incident.alertname}</p>}
      {s.last_decision && (
        <p>Last decision: <Link href={`/decisions/${s.last_decision.id}`}>
          <span className="verdict" data-v={s.last_decision.verdict}>{s.last_decision.verdict}</span></Link></p>
      )}
    </article>
  );
}
```

### `components/Sparkline.tsx`

```tsx
/** 30-day budget-remaining sparkline. Pure SVG, server-rendered; the zero line marks overspend. */
export function Sparkline({ values, width = 220, height = 40 }: { values: (number | null)[]; width?: number; height?: number }) {
  const pts = values.map((v, i) => ({ i, v })).filter((p): p is { i: number; v: number } => p.v !== null);
  if (pts.length < 2) return <p className="muted">Not enough history</p>;
  const lo = Math.min(0, ...pts.map((p) => p.v)), hi = Math.max(100, ...pts.map((p) => p.v));
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * width;
  const y = (v: number) => height - ((v - lo) / (hi - lo)) * height;
  const d = pts.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1]!.v;
  return (
    <svg width={width} height={height} role="img" aria-label={`Budget remaining over 30 days, now ${last.toFixed(1)}%`}>
      <line x1={0} x2={width} y1={y(0)} y2={y(0)} stroke="currentColor" strokeOpacity={0.25} strokeDasharray="3 3" />
      <path d={d} fill="none" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}
```

### `components/RelativeTime.tsx`

```tsx
"use client";
import { useSyncExternalStore } from "react";
import { untilText } from "@/lib/format";

// One shared 30 s clock. The server snapshot is null, so the server HTML and the hydrating
// client render agree; React then switches to the client snapshot and adds the relative part.
let now = Date.now();
function subscribe(onChange: () => void) {
  now = Date.now();                       // React re-reads the snapshot right after subscribing
  const id = setInterval(() => { now = Date.now(); onChange(); }, 30_000);
  return () => clearInterval(id);
}

/** Absolute UTC time is rendered on the server; the relative part only on the client, so the
 *  server and client HTML never disagree (no hydration mismatch). */
export function RelativeTime({ iso }: { iso: string }) {
  const clock = useSyncExternalStore(subscribe, () => now, () => null);
  return (
    <time dateTime={iso} title={iso}>
      {iso.slice(0, 16).replace("T", " ")} UTC{clock !== null ? ` (${untilText(iso, new Date(clock))})` : ""}
    </time>
  );
}
```

### `components/AutoRefresh.tsx`

```tsx
"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-renders server components every `seconds` while the tab is visible. No websockets needed
 *  for numbers that change on a 60-second snapshot cadence. */
export function AutoRefresh({ seconds = 15 }: { seconds?: number }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, seconds * 1000);
    return () => clearInterval(id);
  }, [router, seconds]);
  return null;
}
```

### `components/SessionBadge.tsx`

```tsx
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { SignInButton } from "./SignInButton";

export async function SessionBadge() {
  const session = await auth.api.getSession({ headers: await headers() });
  if (!session) return <SignInButton />;
  return <span className="muted">Signed in as {session.user.name}</span>;
}
```

### `components/SignInButton.tsx`

```tsx
"use client";
import { authClient } from "@/lib/auth-client";

export function SignInButton() {
  return (
    <button type="button" onClick={() => authClient.signIn.social({ provider: "github" })}>
      Sign in with GitHub
    </button>
  );
}
```

### `app/decisions/page.tsx`

```tsx
import Link from "next/link";
import { Suspense } from "react";
import { api } from "@/lib/api";
import { pct } from "@/lib/format";

export const metadata = { title: "Decisions" };

export default function DecisionsPage({ searchParams }: { searchParams: Promise<{ cursor?: string; service?: string }> }) {
  return (
    <main>
      <h1>Decisions</h1>
      <Suspense fallback={<p className="muted">Loading…</p>}>
        <DecisionTable searchParams={searchParams} />
      </Suspense>
    </main>
  );
}

async function DecisionTable({ searchParams }: { searchParams: Promise<{ cursor?: string; service?: string }> }) {
  const { cursor, service } = await searchParams;
  const page = await api.decisions(cursor, service);
  return (
    <>
      <table>
        <thead><tr><th>When (UTC)</th><th>Service</th><th>Commit</th><th>Verdict</th><th>Reason</th><th>Price / affordable</th></tr></thead>
        <tbody>
          {page.items.map((d) => (
            <tr key={d.id}>
              <td className="num">{d.decided_at.slice(0, 16).replace("T", " ")}</td>
              <td>{d.service}</td>
              <td><Link href={`/decisions/${d.id}`}><code>{d.sha.slice(0, 7)}</code></Link></td>
              <td><span className="verdict" data-v={d.verdict}>{d.verdict}</span>{d.override && " (overridden)"}</td>
              <td>{d.reason_code}</td>
              <td className="num">{pct(d.price_pct, 2)} / {pct(d.affordable_pct, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {page.next_cursor && <p><Link href={`/decisions?cursor=${encodeURIComponent(page.next_cursor)}${service ? `&service=${encodeURIComponent(service)}` : ""}`}>Older →</Link></p>}
    </>
  );
}
```

### `app/decisions/[id]/page.tsx`

```tsx
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { ApiError, api } from "@/lib/api";
import { pct } from "@/lib/format";
import { OverrideForm } from "./OverrideForm";
import { ReplayButton } from "./ReplayButton";

export default function DecisionPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <main>
      <Suspense fallback={<p className="muted">Loading decision…</p>}>
        <DecisionView params={params} />
      </Suspense>
    </main>
  );
}

async function DecisionView({ params }: { params: Promise<{ id: string }> }) {
  const id = Number((await params).id);
  if (!Number.isInteger(id) || id <= 0) notFound();
  const d = await api.decision(id).catch((e) => {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  });
  const canOverride = d.overridable;              // the API decides; this only hides the form
  return (
    <>
      <h1>{d.service} · <code>{d.sha.slice(0, 7)}</code></h1>
      <p className="verdict" data-v={d.verdict}><strong>{d.verdict}</strong> — {d.reason_code}</p>
      <table className="num">
        <tbody>
          <tr><th>Price</th><td>{pct(d.price_pct, 2)}</td></tr>
          <tr><th>Affordable</th><td>{pct(d.affordable_pct, 2)}</td></tr>
          <tr><th>Canary</th><td>{d.canary_profile.length ? d.canary_profile.join(" → ") + "%" : "—"}</td></tr>
          <tr><th>Policy</th><td><code>{d.policy_version.slice(0, 7)}</code></td></tr>
          <tr><th>Inputs hash</th><td><code>{d.inputs_hash.slice(0, 16)}…</code></td></tr>
          <tr><th>Decided</th><td>{d.decided_at}</td></tr>
        </tbody>
      </table>
      {d.brief && (
        <section aria-labelledby="brief">
          <h2 id="brief">Freeze Brief <span className="muted">({d.brief.source}{d.brief.fallback_reason ? `: ${d.brief.fallback_reason}` : ""})</span></h2>
          <p style={{ whiteSpace: "pre-line" }}>{d.brief.text}</p>
        </section>
      )}
      {d.override && <p><strong>Overridden</strong> by {d.override.approver}: “{d.override.reason}”</p>}
      <ReplayButton decisionId={d.id} recordedHash={d.inputs_hash} />
      {canOverride && <OverrideForm decisionId={d.id} />}
      <details><summary>Stored inputs</summary><pre>{JSON.stringify(d.inputs, null, 2)}</pre></details>
    </>
  );
}
```

### `app/decisions/[id]/actions.ts`

```ts
"use server";
import { headers } from "next/headers";
import { refresh } from "next/cache";
import { ApiError, api } from "@/lib/api";
import { auth } from "@/lib/auth";
import { OverrideInput, type Replay } from "@/lib/schemas";

export type ReplayState = { result?: Replay; error?: string };
export type OverrideState = { ok?: boolean; error?: string };

/** D-8 on demand: the API recomputes the verdict from the stored inputs and compares hashes. */
export async function replayAction(_: ReplayState, form: FormData): Promise<ReplayState> {
  const id = Number(form.get("decisionId"));
  if (!Number.isInteger(id) || id <= 0) return { error: "Bad decision id" };
  try {
    return { result: await api.replay(id) };
  } catch {
    return { error: "Replay failed; the decision is unchanged" };
  }
}

/** Overrides: the dashboard proves who the human is; the API decides whether they may. */
export async function overrideAction(_: OverrideState, form: FormData): Promise<OverrideState> {
  const h = await headers();
  const session = await auth.api.getSession({ headers: h });
  if (!session) return { error: "Sign in with GitHub first" };

  const parsed = OverrideInput.safeParse({ decisionId: form.get("decisionId"), reason: form.get("reason") });
  if (!parsed.success) return { error: parsed.error.issues[0]?.message ?? "Invalid input" };

  // better-auth 1.7 selects by the local account row id, not by provider name.
  const accounts = await auth.api.listUserAccounts({ headers: h });
  const github = accounts.find((a) => a.providerId === "github");
  if (!github) return { error: "No GitHub account linked to this session" };
  const { accessToken } = await auth.api.getAccessToken({ body: { accountId: github.id }, headers: h });
  if (!accessToken) return { error: "GitHub session expired; sign in again" };
  try {
    await api.override(parsed.data.decisionId, parsed.data.reason, accessToken);
  } catch (e) {
    if (e instanceof ApiError && e.status === 403) return { error: "Your GitHub login is not on the approvers list" };
    if (e instanceof ApiError && e.status === 409) return { error: "Nothing to override: not blocked, already overridden, or too old to re-run" };
    return { error: "Override failed; nothing was approved" };
  }
  refresh();                       // Next 16: re-read uncached data on this page
  return { ok: true };
}
```

### `app/decisions/[id]/OverrideForm.tsx`

```tsx
"use client";
import { useActionState } from "react";
import { overrideAction, type OverrideState } from "./actions";

export function OverrideForm({ decisionId }: { decisionId: number }) {
  const [state, action, pending] = useActionState<OverrideState, FormData>(overrideAction, {});
  if (state.ok) return <p role="status">Override recorded. The deploy job is being re-run and will be admitted on the override lane with a 5% canary.</p>;
  return (
    <form action={action}>
      <h2>Override this block</h2>
      <p className="muted">Recorded with your GitHub login, valid once for 30 minutes, and posted to Slack. The gate re-runs the job; rollbacks and proven fixes never need this.</p>
      <input type="hidden" name="decisionId" value={decisionId} />
      <label htmlFor="reason">Reason (at least 20 characters)</label>
      <textarea id="reason" name="reason" required minLength={20} maxLength={500} rows={3} aria-describedby="override-error" />
      <button type="submit" disabled={pending}>{pending ? "Recording…" : "Override and re-run"}</button>
      {state.error && <p id="override-error" role="alert">{state.error}</p>}
    </form>
  );
}
```

### `app/decisions/[id]/ReplayButton.tsx`

```tsx
"use client";
import { useActionState } from "react";
import { replayAction, type ReplayState } from "./actions";

export function ReplayButton({ decisionId, recordedHash }: { decisionId: number; recordedHash: string }) {
  const [state, action, pending] = useActionState<ReplayState, FormData>(replayAction, {});
  return (
    <form action={action}>
      <input type="hidden" name="decisionId" value={decisionId} />
      <button type="submit" disabled={pending}>{pending ? "Recomputing…" : "Recompute from stored inputs"}</button>
      {state.result && (
        <p role="status">
          {state.result.matches && state.result.inputs_hash === recordedHash
            ? `Same verdict (${state.result.verdict}, ${state.result.reason_code}) from the same inputs.`
            : `MISMATCH: recomputed ${state.result.verdict}. The gate is not deterministic for this record — investigate.`}
        </p>
      )}
      {state.error && <p role="alert">{state.error}</p>}
    </form>
  );
}
```

### `app/policy/page.tsx`

```tsx
import { Suspense } from "react";
import { api } from "@/lib/api";

export const metadata = { title: "Policy" };

export default function PolicyPage() {
  return (
    <main>
      <h1>Freeze policy versions</h1>
      <p className="muted">Each version merged only after its backtest passed in CI. The report is the one posted on the pull request.</p>
      <Suspense fallback={<p className="muted">Loading…</p>}><Versions /></Suspense>
    </main>
  );
}

async function Versions() {
  const versions = await api.policies();
  return versions.map((v) => (
    <section key={v.version}>
      <h2><code>{v.version.slice(0, 7)}</code> {v.active && <span className="badge">active</span>}</h2>
      <p className="muted">Created {v.created_at.slice(0, 10)}</p>
      {v.backtest_markdown ? <pre>{v.backtest_markdown}</pre> : <p className="muted">No backtest stored.</p>}
    </section>
  ));
}
```

### `vitest.config.ts`

```ts
import { defineConfig } from "vitest/config";
export default defineConfig({ test: { include: ["tests/**/*.test.ts"] } });
```

### `tests/format.test.ts`

```ts
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
```

### `tests/schemas.test.ts`

```ts
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
```

### `e2e/freeze.spec.ts`

```ts
// Runs against `next start` with BUDGETGUARD_API pointing at e2e/fake-api.mjs (fixtures from the
// platform's OpenAPI examples). Not executed in the skill-authoring session; run it in CI.
import { expect, test } from "@playwright/test";

test("an unknown service says why, in words", async ({ page }) => {
  await page.goto("/");
  const card = page.getByRole("article", { name: "inventory-service" });
  await expect(card.getByText("Unknown — no trustworthy signal")).toBeVisible();
  await expect(card.getByText("No metrics at all")).toBeVisible();
});

test("a blocked decision shows price vs affordable and replays identically", async ({ page }) => {
  await page.goto("/decisions/4812");
  await expect(page.getByText("block")).toBeVisible();
  await page.getByRole("button", { name: "Recompute from stored inputs" }).click();
  await expect(page.getByRole("status")).toContainText("Same verdict");
});

test("override needs a signed-in approver and a 20-character reason", async ({ page }) => {
  await page.goto("/decisions/4812");
  await expect(page.getByRole("button", { name: "Override and re-run" })).toBeVisible();
  await page.getByLabel("Reason (at least 20 characters)").fill("too short");
  await page.getByRole("button", { name: "Override and re-run" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
});
```

### `Dockerfile`

```dockerfile
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:24-alpine AS build
WORKDIR /app
# Better Auth logs an error during `next build` without a secret; this value exists only in the
# build stage and never reaches the runtime image.
ENV NEXT_TELEMETRY_DISABLED=1 BETTER_AUTH_SECRET=build-stage-placeholder-not-used-at-runtime
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:24-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S -g 10001 web && adduser -S -u 10001 -G web web
COPY --from=build --chown=web:web /app/.next/standalone ./
COPY --from=build --chown=web:web /app/.next/static ./.next/static
USER 10001
EXPOSE 3000
CMD ["node", "server.js"]
```
