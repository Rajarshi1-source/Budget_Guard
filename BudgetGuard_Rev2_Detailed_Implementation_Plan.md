# BudgetGuard — Detailed Implementation Plan (Rev 2.1)

25 Oct 2026 · built on *BudgetGuard Rev 2 — Implementation Plan* (10 Oct 2026)

This is the build plan: what to build, in what order, in which files, how to test each piece,
and what "done" means for every week. It covers the MVP (end of week 5) and all eight
differentiators (D-1 to D-8).

---

## 0. How to read this plan

### 0.1 Sources and precedence

| Priority | Source | How it is used |
| --- | --- | --- |
| 1 | `BudgetGuard Rev 2 — Implementation Plan.md` | **Single source of truth** for scope, architecture, versions, SLOs and schedule |
| 2 | The eight BudgetGuard skills in `.claude/skills/` | Project-wide rules and verified code. They follow Rev 2 and record the agreed deviations, each flagged with the reason. Where a skill says "Rev 2 is wrong here, fixed", the skill wins, and §3 lists every such case |
| 3 | `BudgetGuard_Implementation_Plan.md` (Rev 1) | Used **only** where Rev 2 is silent, and only after checking it against Rev 2's defect list (§B). §15 lists what was adopted and what was rejected |
| 4 | Earlier decisions in this project (Maven, Spring Boot 4.1.2, the dependency set, FastAPI 0.143.0, Next.js 16.3.8, Better Auth) | Recorded in the decision register (§3) |

### 0.2 Skill to open per area (the global rules)

| Area | Skill | Reference file |
| --- | --- | --- |
| Budget math, integrity, unfreeze ETA | `budgetguard-slo-math` | — |
| SLO specs, rule generator, promtool, Alertmanager | `budgetguard-slo-rules` | — |
| The four Spring Boot services | `budgetguard-spring-services` | — |
| Gate, pricing, lanes, GitHub App, overrides, **security and API contract** | `budgetguard-admission-gate` | `github-app.md`, `security-and-api.md` |
| Schema, snapshotter, Valkey | `budgetguard-data-layer` | `schema-and-queries.md` |
| Backtester, Freeze Brief, grounding eval | `budgetguard-mlops-evals` | `eval-harness.md` |
| Compose, Helm, k3s, Argo Rollouts, workflows | `budgetguard-delivery-platform` | `manifests-and-workflows.md` |
| Next.js dashboard | `budgetguard-nextjs-dashboard` | `dashboard-code.md` |

Starter code (platform skeleton, root files, tests): `.claude/skills/BudgetGuard_starter_code.md`.

Every skill ends with a `MANDATE` line. Those mandates are the non-negotiables of this plan:

- The verdict is deterministic and recorded before GitHub is answered. A rollback is always
  admitted. The LLM never decides.
- Rules are generated from one spec and never edited by hand. Every window an alert
  references has a recording rule and a promtool test. Silence is not health.
- Pure functions with no I/O for the math. 14.4x spends 2% of a 30-day budget per hour and
  lasts 50 hours. Missing data is UNKNOWN, never zero errors.
- Spring Boot 4.1.x, Java 21, Maven, never 3.x. Label cardinality stays bounded. The services
  emit truth and decide nothing.
- Prometheus stays the live source of truth. Buckets are summed, never averaged. Valkey is
  never a source of truth.
- Every number in a brief comes from the decision record.
- Deploy by digest, verify the signature, and gate the job on the rollout result, not on
  `helm --wait`.
- The browser never calls the platform API. The dashboard explains decisions and never makes
  one.

### 0.3 Conventions in this document

- **WP** = work package (a unit of one to two days). IDs look like `W3.2` (week 3, package 2).
- **Exit** = a check anyone can run that proves the week is done.
- File paths are relative to the repository root.
- "[new]" marks something this plan adds on top of Rev 2 and the skills, always with a reason.

---

## 1. Where the project stands today (checked 25 Oct 2026)

### 1.1 What is in the repository

| Item | State | Notes |
| --- | --- | --- |
| `BudgetGuard Rev 2 — Implementation Plan.md` | Present | Source of truth |
| `BudgetGuard_Implementation_Plan.md` | Present | Rev 1; superseded |
| `Architecture_Diagram_Preview.webp` | Present | Preview only; redraw for the README in week 8 |
| `.claude/skills/*.skill`, `*.SKILL.md`, reference copies, eval report, evals zip | Present | See 1.3: **not active as skills yet** |
| `auth-service.zip`, `orders-service.zip`, `inventory-service.zip`, `notifications-service.zip` | Present, **not extracted** | Spring Initializr output; see 1.2 |
| `services/`, `platform/`, `slo/`, `rules/`, `dashboard/`, `deploy/`, `.github/` | **Absent** | No application code is committed yet |

So the implemented code in the repository is **zero**. The starter code, platform skeleton,
dashboard and eval harness exist only as tested code inside the skill documents. They were
run in the skill-authoring session and must be copied into the repository during the weeks
below.

### 1.2 The four Initializr projects

Checked by extracting the zips:

| Property | Value | Matches the decision? |
| --- | --- | --- |
| Spring Boot parent | `4.1.2` | Yes (Rev 2 needs 4.1.1 or newer) |
| Java | `21` | Yes |
| Group / packages | `dev.budgetguard`, `dev.budgetguard.{auth,orders,inventory,notifications}` | Yes. Rev 2's Java snippets use `com.budgetguard.common.metrics`; the skills use `dev.budgetguard.*`. Keep `dev.budgetguard` |
| Dependencies (all four) | `spring-boot-starter-webmvc`, `-actuator`, `-validation`, `micrometer-registry-prometheus`, configuration processor, `spring-boot-devtools` (optional, runtime) + Boot 4 test starters | Yes |
| orders-service extra | `spring-boot-starter-restclient` | Yes |
| Config file | `application.yaml` with only `spring.application.name` | Rename to `application.yml` or keep `.yaml`; pick one and use it everywhere (skills write `application.yml`) |
| Version | `0.0.1-SNAPSHOT`, empty `<name/>`, `<licenses>`, `<scm>` blocks | Replace with the reactor version `1.0.0`; delete the empty blocks |
| Maven wrapper | `wrapperVersion=3.3.4`, **`distributionUrl` → Maven 3.10.0** | **Mismatch**: Rev 2 pins Maven 3.9.x, and this machine has 3.9.16. Decision P-1 in §3.3 |

### 1.3 The skills are present but not loaded

Claude Code discovers project skills only as folders: `.claude/skills/<skill-name>/SKILL.md`
(with `references/` beside it). The repository holds them as flat files
(`budgetguard-*.SKILL.md`) and zip archives (`*.skill`), so **none of the eight skills is
active**. They did not appear in this session's skill list. Week 0 fixes that (W0.2).

Validation run today with the same checks as skill-creator's `quick_validate` (frontmatter
keys, kebab-case name matching the folder, description ≤ 1024 characters and free of angle
brackets, every `references/` file that SKILL.md names exists):

| Skill | Description length | Result |
| --- | --- | --- |
| admission-gate | 1019 (changed today, see 1.5) | pass |
| data-layer | 954 | pass |
| delivery-platform | 991 | pass |
| mlops-evals | 951 | pass |
| nextjs-dashboard | 1000 | pass |
| slo-math | 972 | pass |
| slo-rules | **1020** | pass (the earlier note said every description is 951–1000 characters; this one is 1020, still under the 1024 limit) |
| spring-services | 981 | pass |

### 1.4 This machine (Windows 11 + WSL 2)

| Tool | Where | Version | Needed for | Status |
| --- | --- | --- | --- | --- |
| Java | Windows | Oracle JDK 21.0.11 | services | OK |
| Maven | Windows | 3.9.16 | services | OK |
| Python / uv | Windows | 3.14.6 / 0.11.6 | platform | OK |
| Node | Windows | 24.12.0 | dashboard | OK |
| Helm | Windows (C:) | 4.2.4 | charts | OK; rendered both charts today |
| kind | Windows (C:) | 0.32.0 | local Kubernetes | Installed, but **cannot reach Docker**: it shells out to a `docker` CLI and Windows has none (`kind get clusters` fails with `exec: "docker": executable file not found`) |
| kubectl | Windows | 1.36.4 | cluster | OK |
| Docker Engine | WSL 2 distro **`Ubuntu-D`** (on D:, the default distro) | 29.9.0, Compose v5.6.0, cgroup v2, systemd active, user in `docker` group | compose, testcontainers, promtool, kubeconform, image builds | OK; used today for promtool, TimescaleDB and kubeconform. No TCP listener (socket only) |
| Second distro `Ubuntu` | WSL 2 | — | — | Has no Docker and fails to start a systemd user session; always pass `-d Ubuntu-D` |
| kind, kubectl, helm | inside `Ubuntu-D` (`~/.local/bin`) | v0.33.0, v1.36.5, v4.3.1 | local Kubernetes | **Installed and tested 25 Oct** (kind cluster on `kindest/node:v1.36.4` created, reached from Windows, deleted) |
| make, k6, uv | inside `Ubuntu-D` | not installed | Makefile, load, integration tests | `make` needs `sudo apt-get install make` (your password); see §6 W0.6 |
| make | Windows | winget `ezwinports.make` | — | Present; Makefile targets that call `docker` still run in WSL |

The working split this implies is in §6 W0.6: build and unit-test on Windows; run
everything that needs a container engine inside `Ubuntu-D`.

### 1.5 Done today

- Wrote `references/security-and-api.md` for `budgetguard-admission-gate`: every endpoint
  with caller, auth, request, response and errors; what the internet can reach; the secrets
  inventory with rotation steps; a 12-threat model; the security test list; and 18 changes it
  introduces (§11 of that file).
- Updated `references/github-app.md` to match the contract: body size cap, current and
  previous webhook secret, repo and installation check, `deployment_id` in the job,
  `validate_job()` before any token is minted, and installation tokens limited to this
  repository and one permission.
- Ran that code against 28 pytest cases (Python 3.14.6, FastAPI 0.143.0): all pass.
- Updated the admission-gate SKILL.md (reference table, App registered as private, the
  incident-fix lane needs a firing `ALERTS` series, approvers by numeric id, the
  reconcilers, new tests and soundbites; description +23 characters to mention endpoint auth
  and secrets). Repackaged `budgetguard-admission-gate.skill`, refreshed the readable copy,
  added `BudgetGuard_security-and-api.md` and a 13th concern to the skills README.
- The routing eval (80/80) ran before today's description change. It was a small wording
  addition, but it has not been re-measured.

### 1.6 The earlier skill-building report, checked claim by claim (25 Oct 2026)

Everything that could be re-run here was re-run from the code inside the skill documents
themselves (extracted to a scratch directory, never edited by hand except for the fixes listed).

| Claim | Result | Evidence |
| --- | --- | --- |
| Eight skills, all pass `quick_validate` | **True** | Same checks re-run on all eight |
| Descriptions are 951–1000 characters | **Not quite** | `slo-rules` is 1020 (still under the 1024 limit); `admission-gate` is now 1019 after today's change |
| Files are in `dist/` | **Different location** | There is no `dist/`; everything is in `.claude/skills/` (and not loadable as skills, §1.3) |
| Budget math tests | **True**: 12 pass | Python 3.14.6 |
| Backtester, brief writer, grounding eval: 34 pass | **True after a fix**: 34 pass | The test read `platform/config/freeze-policy.yaml`, but the layout and workflows put the policy in repo-root `policy/`; fixed in `eval-harness.md`. Template brief eval: 120/120 (Wilson lower bound 0.969). `backtest.cli score`: 614 cases, no invariant violations, rollback admit rate 1.0 |
| Starter platform API: 14 pass | **True**, now on the target versions | 14 pass on Python 3.14.6 + FastAPI 0.143.0 (the report ran 3.13 + 0.162). After today's security changes the starter has **20** tests, all pass |
| Dashboard: `tsc` clean, 8 Vitest, `next build` succeeds | **True** | Reproduced; all pages Partial Prerender. **But** `npm run lint`, which `ci.yml` runs, failed: no ESLint and no config (Next 16 removed `next lint`), and one real rule violation (`setState` inside `useEffect` in `RelativeTime`). Both fixed in `dashboard-code.md`; lint now clean |
| Helm templates not rendered | **Now rendered** | Windows Helm 4.2.4: `helm lint` passes for both charts; 11 rendered objects pass `kubeconform -strict` against Kubernetes 1.36 schemas and the CRD catalog (Rollout, AnalysisTemplate, TraefikService, IngressRoute, PodMonitor). The digest `required` guard works; Argo's `{{args.x}}` survives rendering |
| promtool rules tests passed (3.13.2) | **True on 3.13.5** | Generator output is byte-stable; `promtool check rules` (26 rules) and the skill's tests pass |
| Migrations and PostgreSQL store "need a real database" | **Now run** | `001`–`003` and every query block ran on `timescale/timescaledb:2.27.0-pg18` **and** `2.31.0-pg18`. **Two real query bugs found and fixed**: `round()` on `DOUBLE PRECISION` in the chargeback query (Rev 2 §5 query 4 has it too) and an ungrouped `o.objective` in the change-failure cost subquery (would have failed the week 6 deploy-events worker). The starter's PgStore SQL also ran: a replayed body with a new delivery id is stopped by `github_deployment_id`, the incident upsert is idempotent, the override flow works |
| Gate worker untested | **Still true** | It compiles; it needs a webhook → worker → callback integration test (W5) |
| Spring services code | **Not claimed by the report; checked anyway** | Compiling the skill's `bg-common` classes against Boot 4.1.2 failed: `MeterRegistryCustomizer` moved to `org.springframework.boot.micrometer.metrics.autoconfigure` in Boot 4. `FaultState` was described but never written. Both fixed (new `references/fault-admin.md`); all 11 classes compile with Resilience4j 2.4.0 |
| Routing 80/80; with/without 56/56 vs 43/56; 36/36 vs 23/36 uncontaminated; about $8 | **Consistent with the saved results** | Recounted from `results_round*.json` and `functional_results.json` in the evals zip: 80/80, 56/56 vs 43/56, 36/36 vs 23/36, $7.93. Four of the five contaminated baselines quote the skill files in their saved reply; the fifth isn't visible in the text. Not re-run (it costs model usage) |
| Rev 2 problems 1–7 | **All confirmed against the Rev 2 text** | §4.11 inputs (1), §8 override path (2), §10.3 `Case` with a priced `GateInput` (3), §10.5 regex over `str(record)` (4), §9.5 replica note and internal calls (5), §10.2 `--wait` (6), §5 `fingerprint UNIQUE` (7) |
| Versions (point 8) | **Partly outdated, see §6 W0.1** | FastAPI 0.162 latest and 0.143.0 still the newest release older than two weeks (8 Oct): **true**. Next.js 16.4.x latest (16.4.3), 16.3.8 published 30 Sep: **true**. Better Auth 1.7.12: **true**. **Resilience4j "3.x" does not exist**: Maven Central's newest is **2.4.0**. Newer patch releases exist for Prometheus, Argo Rollouts, Helm, TimescaleDB, Grafana and k3s |

All fixes were written back into the skill documents and the five affected packages were
rebuilt: `admission-gate`, `data-layer`, `mlops-evals`, `nextjs-dashboard`, `spring-services`.
The starter code was also updated.

---

## 2. Scope: MVP and the eight differentiators

### 2.1 MVP (complete at the end of week 5)

Rev 2 §2 cut line, with what each item means in this plan:

| # | MVP item | Built in | Exit evidence |
| --- | --- | --- | --- |
| M1 | Four services with fault flags and `error_origin` labels | W1 | `/actuator/prometheus` shows `error_origin="dep:auth-service"` under `FAULT_DEPENDENCY` |
| M2 | SLO specs, generated rules and alerts, promtool fixtures | W2 | `bg generate --check` and `promtool test rules` green in CI |
| M3 | Tri-state SLIs with traffic floors and blackbox probes (D-4) | W3 | Killing a pod gives UNKNOWN on the card and `SLIIntegrityLost` fires |
| M4 | Snapshotter writing 5-minute good/total buckets | W4 | 30-day availability from TimescaleDB within 0.01 pp of Prometheus |
| M5 | Gate API, GitHub App protection rule, decision records (D-1) | W5 | A blocked deploy shows "Rejected by BudgetGuard" with price and ETA in the run |
| M6 | Exact unfreeze ETA (D-5) | W3 (math), W5 (wired) | ETA in the rejection comment and on the card |
| M7 | Grafana: burn rate, 30-day availability, MTTR per incident type, deploy change rate | W4 | Four provisioned panels show data |
| M8 | Compose for local, Helm on k3s for public, CI/CD to Docker Hub | W1, W4, W5 | `make up` works; the public URL serves the dashboard |
| M9 | **[new]** Security baseline from `security-and-api.md` §11 items 1–10 and 18 | W4–W5 | Security tests in `platform/tests/test_security.py` pass |

### 2.2 The eight differentiators

| ID | Differentiator | Phase | Depends on | Exit evidence | Section |
| --- | --- | --- | --- | --- | --- |
| D-1 | Unbypassable admission: GitHub App protection rule + Argo Rollouts analysis | MVP, W5 | W1–W4 | Deleting `environment:` from `deploy.yml` makes the job fail for lack of `KUBECONFIG`; the protection rule still answers | §13.1 |
| D-2 | Deploy pricing: posterior change-failure rate × risk multiplier × median failure cost vs affordable | W6 (engine shipped W5 with defaults) | W5 | The Rev 2 §D worked example reproduces from real rows | §13.2 |
| D-3 | Culprit, not victim: `error_origin` chargeback, `attribution: charge_origin` | Labels W1; gate W6 | W1, W5 | `make demo-dependency-outage`: auth frozen, orders not | §13.3 |
| D-4 | Truthful SLIs: tri-state, traffic floor, `up`/`absent`/probe checks | MVP, W3 | W1, W2 | Dead service → UNKNOWN, never HEALTHY | §13.4 |
| D-5 | Exact unfreeze ETA from replayed buckets | MVP, W3 math, W5 wiring | W4 | ETA test fixtures (7 h 20 m vs 27 days) pass; ETA shown in the comment | §13.5 |
| D-6 | Policy backtesting in CI | W7 | W5 (facts recorded from the first decision), W6 | A policy PR gets a backtest table; a rollback block fails CI | §13.6 |
| D-7 | Budget-proportional canary with real traffic weights | W8 (stretch) | W5 | A 5% step sends about 5% of requests, measured; abort on self-burn ratio > 2 | §13.7 |
| D-8 | Interop and replayable decisions | Records W5; Recompute + Sloth parity W8 | W2, W5 | "Recompute" shows the same hash and verdict; the Sloth parity test passes | §13.8 |

Plus the §E Freeze Brief (LLM narrative with template fallback and grounding eval) in W7.

### 2.3 Out of scope (said in the README "Known limits")

High-availability PostgreSQL (CloudNativePG named as the path), Thanos/Mimir (named for 100+
services), a service mesh (named for L7 SLIs), service-to-service authentication, Slack-button
overrides (overrides go through the dashboard only, so there is one approval path to secure),
SLA tracking, ML forecasting of exhaustion (the deterministic ETA replaces it), PagerDuty.

---

## 3. Decision register

### 3.1 Decisions taken (Rev 2, your earlier choices, skill fixes)

| ID | Decision | Source | Why |
| --- | --- | --- | --- |
| A-01 | Spring Boot **4.1.2**, Java 21, Maven multi-module reactor `services/` with `bg-common` | Your choice; Rev 2 §C (≥ 4.1.1) | Initializr's current default; 3.5 OSS support ended 30 Jun 2026 |
| A-02 | Starters: webmvc, actuator, Prometheus registry, validation, configuration processor; restclient for orders only. No JPA, MySQL, Security, Lombok, Redis | Your choice | Services store nothing and need no login; Security would also block `/actuator/prometheus` |
| A-03 | Resilience4j **2.4.0** core modules (`resilience4j-circuitbreaker`, `resilience4j-micrometer`) wired by hand in `bg-common`, not a starter | spring-services skill, corrected 25 Oct | Rev 2's "3.x" does not exist; 2.4.0 compiles with Boot 4.1.2 (§1.6) |
| A-04 | Platform: Python 3.14, **FastAPI 0.143.0** pinned, Pydantic 2, SQLAlchemy 2 async, asyncpg, redis-py, PyJWT | Your choice (Rev 2 said 0.128.x) | 0.162 is current, but about 20 releases landed in one week; 0.143.0 was the newest release older than two weeks on 24 Oct |
| A-05 | Dashboard: Node 24, **Next.js 16.3.8**, React 19.2, zod 4, **Better Auth 1.7.x** | Your choice | 16.4 shipped 6 Oct; trial it on a branch. Auth.js is now maintained by the Better Auth team |
| A-06 | Gate chain order: `rollback → override → integrity → incident_fix → hard_stop → price` | admission-gate skill | Rev 2's code is right and its prose is wrong; the override lane is new |
| A-07 | `attribution: charge_origin` policy switch (default) | admission-gate skill (concern 1) | Rev 2's gate read all errors and any page, so the victim of a dependency outage froze |
| A-08 | Override = single-use, 30-minute record + **re-run of the failed job**; App needs Actions: read & write | admission-gate skill (concern 2) | GitHub consumes the callback when the gate rejects |
| A-09 | Lanes are proven: rollback by service subtree SHA; incident fix by an open incident **and a firing `ALERTS` series**; override by an unconsumed row | admission-gate skill + today's T-6 | A label anyone can add is not a security boundary |
| A-10 | `LOW_TRAFFIC` prices normally against the 30-day budget, skips the short-window hard stop, forces the conservative canary (policy flag `low_traffic_prices_normally: true`) | slo-math + admission-gate skills | Rev 2 would page on-call for every night-time deploy of a quiet service |
| A-11 | Backtest cases store raw `DeployFacts`; each policy prices them | mlops-evals skill (concern 3) | A stored price makes weight changes replay identically |
| A-12 | Grounding check rewritten: identifiers first, numbers at printed precision, forbidden phrases, required facts; runs online too | mlops-evals skill (concern 4) | SHA digits let invented numbers pass; dates parsed as negatives |
| A-13 | Service-to-service calls go through Traefik's internal entrypoint; otherwise the README says "replica-weighted canary" | delivery skill (concern 5) | A 5% step on 4 replicas is 25%, and internal calls never pass the ingress |
| A-14 | Deploy job waits on `kubectl argo rollouts status`, not `helm --wait` | delivery skill (concern 6) | Helm 4's kstatus wait ignores canary analysis |
| A-15 | `incidents` unique on `(fingerprint, started_at)` | data-layer skill (concern 7) | The same alert recurring next week has the same fingerprint |
| A-16 | `/v1/decisions` reachable through the tunnel, protected by GitHub OIDC | delivery skill | GitHub-hosted runners call it |
| A-17 | `cosign verify` before Helm, against `build-push.yml@refs/heads/main` (+ workflow SHA, today) | delivery skill + security §11 #13 | Rev 2 signed images and never verified them |
| A-18 | Alembic is the only schema owner; no `docker-entrypoint-initdb.d` mount | delivery skill | Init scripts run once and drift from migrations |
| A-19 | Snapshotter leader = PostgreSQL advisory lock (Rev 2 said Kubernetes Lease) | data-layer skill | Same guarantee in compose and k3s, no RBAC |
| A-20 | `GET /v1/decisions` derives `bake` from `canary_profile` | delivery skill | Rev 2's job read a field the gate never set |
| A-21 | Alertmanager page route has `continue: true` into the `budgetguard` receiver | slo-rules + delivery skills | Otherwise pages never reach the incident table |
| A-22 | The 6x/6h tier **pages** (Workbook); `for:` only on `SLIIntegrityLost` | Rev 2 §4.2, B-12 | Short window already confirms |
| A-23 | Verdict-cache invalidation by per-service generation counter `bg:verdict-gen:{service}` | data-layer skill | O(1), no `SCAN` |
| A-24 | LLM: adapter default `gpt-5.4-mini`, challenger `gpt-6-luna` (nightly, never served), template fallback; model name only in `config/llm.yaml` | Rev 2 §E | Re-check prices at kickoff |
| A-25 | PostgreSQL 18 + TimescaleDB 2.27.x **TSL** image (never `-oss`), Valkey 9.1 | Rev 2 §C, data-layer skill | Continuous aggregates and compression are TSL features |
| A-26 | **[new, today]** Security controls in `security-and-api.md` §11 (18 items), scheduled in §10 of this plan | security-and-api.md | See that file's threat model |
| A-27 | Notifications SLI measures delivery from the queue (`notification_delivery_seconds`), not the HTTP 202 | spring-services skill | Otherwise the SLI stays green while messages rot |
| A-28 | `service` metric tag from a `MeterRegistryCustomizer` bean, not a property | spring-services skill | Works whatever Boot 4.1 calls the common-tags property |

### 3.2 Rev 1 ideas checked and adopted

Rev 1 was consulted only where Rev 2 is silent. Each item was checked against Rev 2's defect
list before adoption.

| Rev 1 idea | Verdict | Where |
| --- | --- | --- |
| Rate limiter on `POST /login` (credential stuffing) | **Adopt**, returning 429. A 429 is 4xx, so it does not burn the availability budget | W1.3 |
| "Budget recovered" Slack message when a frozen service becomes affordable again | **Adopt** as a Freeze Brief variant (`kind: recovered`), template only | W7.6 |
| Freeze impact view (freeze minutes, blocked deploys per service) | **Adopt** as a SQL view over `decisions` (Rev 2 has the data, not the view); feeds D-6's "false-freeze minutes" | W6.6 |
| Mobile-friendly, dark-mode dashboard | **Adopt** (CSS tokens with dark mode; no extra library) | W4.7 |
| Inventory serves stale cached stock on failure | Already in Rev 2 §8 | W1.3 |
| Locust next to k6 | Reject: duplicates k6, which Rev 2 pins | — |
| Fail-open when BudgetGuard is down | Reject: Rev 2 corrects it. An unanswered protection rule is fail-closed; the audited break-glass is the admin bypass | — |
| `remote_write` into TimescaleDB | Reject: needs Promscale, discontinued (B-11) | — |
| `freeze_events` table, `/api/deployments/record` endpoint | Reject: replaced by `decisions` and the `deployment_status` webhook | — |
| Binary freeze below 10% | Reject: replaced by priced admission (D-2) | — |
| Mean MTTR, `TIMESTAMP` without zone, 260 M rows | Reject: B-14, B-11 | — |

### 3.3 Decisions you need to make (with a recommendation)

| ID | Question | Recommendation |
| --- | --- | --- |
| P-1 | Maven wrapper points to 3.10.0; Rev 2 pins 3.9.x | Set `distributionUrl` to 3.9.16 (matches this machine and CI's `setup-java` Maven), and revisit 3.10 after the MVP |
| P-2 | Install the skills as folders so Claude Code loads them | Yes (W0.2). Move the `.skill` zips, flat copies and eval bundle to `docs/skills-dist/` so `.claude/skills/` holds only the eight folders |
| P-3 | Add a project `CLAUDE.md` stating the precedence rule in §0.1 and the mandates | Yes (W0.3); it is loaded every session |
| P-4 | How Windows-side tools reach the Docker Engine in `Ubuntu-D` (§6 W0.6) | **Decided and tested**: the container and Kubernetes toolchain runs inside `Ubuntu-D`; Windows keeps its installs as fallbacks and can reach the kind cluster through the exported kubeconfig. No daemon port is opened |
| P-9 | Pin the patch releases checked on 25 Oct (§6 W0.1), or keep Rev 2's older pins | Take the newer patches inside the same minor lines (they are bug and CVE fixes); keep the minors Rev 2 chose |
| P-5 | Domain for the tunnel (`budgetguard.<your-domain>`), Cloudflare account, Oracle Cloud account | Needed by W5.10; start the Oracle sign-up in week 1 (ARM capacity can take days) |
| P-6 | Public GitHub repository (custom protection rules are free on public repos) | Yes; confirm the feature's current plan requirements at kickoff |
| P-7 | Slack workspace with `#bg-page`, `#bg-tickets`, `#bg-briefs` | Optional for the MVP; templates still render on the PR |
| P-8 | Keep `spring-boot-devtools` | Keep as optional runtime (Boot leaves it out of the packaged jar) |

---

## 4. Target architecture (summary)

Rev 2 §3 stands. Four planes and two enforcement points; Prometheus is the live truth;
TimescaleDB is the replayable history.

```text
  Workload (bg-apps)            Signal (monitoring)               Decision (bg-platform)                 Enforcement
  auth  orders  inventory  ──►  Prometheus 3.13 (35d) ──ALERTS──►  platform-api (FastAPI)  ◄── webhook ──  GitHub App protection rule
  notifications (Boot 4.1)      Alertmanager 0.34.1  ──webhook──►  gate-worker ── callback ────────────►  (production-<service>)
  error_origin, SLO buckets     blackbox exporter                  snapshotter (advisory lock)
        ▲                              │                           deploy-events, brief-writer          Argo Rollouts canary
        │                              └──── scrape ◄──────────    reconcilers [new]                     + AnalysisTemplate
        │                                                                 │
        └────── Traefik internal entrypoint (canary weights) ◄────────────┘ canary_profile
  State (data): PostgreSQL 18 + TimescaleDB (system of record) · Valkey 9.1 (cache, locks, streams)
  People: Next.js dashboard (BFF) · Grafana (anonymous Viewer) · Slack
```

Flows (Rev 2 §3, with corrections):

1. **Pre-deploy admission.** Protection-rule webhook → HMAC, repo/installation check →
   `enqueue_once` → worker re-validates the job → single-flight lock → verdict cache (by
   generation) → gather inputs concurrently (3 s timeouts) → `DeployFacts` → `GateInput` →
   `evaluate()` → **one transaction** (decision row with `inputs.facts`, deployments row,
   override consumed) → callback with the template comment (retried up to 30 min) →
   `XADD bg:briefs` → ack.
2. **In-flight enforcement.** Helm renders Rollout steps from `canary_profile` + `bake`; the
   AnalysisTemplate aborts when canary self-burn exceeds 2x stable for two checks or the canary
   has no traffic; the job fails on abort (`kubectl argo rollouts status`).
3. **Alert → incident.** Alertmanager → `/v1/webhooks/alertmanager` → upsert incident on
   `(fingerprint, started_at)` → bump `bg:verdict-gen:{service}`; resolved closes it.
4. **Snapshots.** Every 60 s the leader writes the last aligned 5-minute bucket per SLO and
   back-fills gaps from the last 6 h.
5. **Deploy outcome and DORA.** `deployment_status` → `bg:deploy-events` → deployments row →
   60 minutes later, only self-inflicted burn can mark a change failure.
6. **[new] Reconcilers** (`workers/reconcilers.py`): failed-webhook redelivery (GitHub does not
   redeliver on its own), admin-bypass ingestion, environment drift, cluster digest drift.

---

## 5. Target repository layout

```text
budgetguard/
├── CLAUDE.md                              # [new] precedence rule + mandates (W0.3)
├── Makefile                               # up, down, demo-*, test, sloth-parity
├── .env.example  .gitignore  .gitleaks.toml
├── .github/
│   ├── CODEOWNERS                         # policy, gate, slo, rules, **workflows, deploy** [new]
│   └── workflows/ ci.yml build-push.yml deploy.yml policy-backtest.yml brief-eval.yml
├── services/                              # Maven reactor (W1)
│   ├── pom.xml  mvnw  mvnw.cmd  .mvn/
│   ├── bg-common/src/main/java/dev/budgetguard/common/{metrics,http,faults}/
│   ├── auth-service/  orders-service/  inventory-service/  notifications-service/
├── slo/{auth,orders,inventory,notifications}-service.yaml          # W2
├── policy/freeze-policy.yaml                                       # W5, backtested from W7
├── rules/{generated,tests,parity}/                                 # W2, parity W8
├── platform/
│   ├── pyproject.toml  uv.lock  Dockerfile  alembic.ini
│   ├── budgetguard/
│   │   ├── config.py store.py pgstore.py queue.py auth.py edge.py[new] incidents.py api.py main.py
│   │   ├── budget.py integrity.py eta.py                           # W3
│   │   ├── gate.py pricing.py lanes.py changes.py records.py       # W5–W6
│   │   ├── briefs.py slo_registry.py                               # W7, W4
│   │   ├── rulegen/{spec,generate,sloth,cli}.py                    # W2
│   │   ├── integrations/{github_app,callbacks,prometheus,slack,llm}.py
│   │   └── workers/{gate_worker,snapshotter,deploy_events,brief_writer,reconcilers[new]}.py
│   ├── backtest/  evals/  prompts/  config/llm.yaml                # W7
│   ├── migrations/versions/ 001_schema 002_timeseries 003_seed 004_roles[new] 005_eval_runs
│   ├── sql/                                                        # raw SQL the migrations run
│   └── tests/
├── dashboard/                                                      # W4–W5, W8
├── grafana/{datasources,dashboards}/
├── load/{steady,merge-storm,scenarios}.js
├── deploy/
│   ├── compose/{docker-compose.yml,.env.versions,prometheus.yml,alertmanager.yml,blackbox.yml}
│   ├── helm/{service,platform}/
│   └── k3s/{kps-values.yaml,cloudflared-config.yaml,rbac.yaml,networkpolicies.yaml,rules/}
└── docs/{decisions.md,runbooks/,skills-dist/}
```

---

## 6. Week 0 — Kickoff (2 days)

Goal: a clean repository that builds an empty reactor, loads the skills, and pins versions.

### W0.1 Version re-check (half a day)

Most rows were checked on 25 Oct 2026 against PyPI, npm, Maven Central, GitHub releases and
Docker Hub. Re-check on the day you start, then write the results into
`deploy/compose/.env.versions` and `docs/decisions.md`.

| Component | Rev 2 / skill pin | Checked 25 Oct 2026 | Pin to use |
| --- | --- | --- | --- |
| Spring Boot | 4.1.x (≥ 4.1.1) | 4.1.2 released 22 Oct (also 4.0.9) | **4.1.2** (what Initializr generated) |
| Resilience4j | "3.x" | **no 3.x exists**; Maven Central newest 2.4.0 | **2.4.0** (compiled with Boot 4.1.2) |
| FastAPI | 0.128.x → 0.143.0 | latest 0.162.0 (23 Oct); 0.143.0 (8 Oct) is still the newest older than two weeks | **0.143.0** |
| Next.js | 16.3.x → 16.3.8 | latest 16.4.3; 16.3.8 published 30 Sep | **16.3.8**; trial 16.4 on a branch |
| Better Auth | 1.7.x | 1.7.12 | **1.7.12** |
| ESLint / eslint-config-next | — (missing) | ESLint 10.13.0 latest, 9.39.5 newest 9.x; eslint-config-next 16.3.8 accepts ESLint ≥ 9 | **9.39.x / 16.3.8** (tested) |
| Prometheus | 3.13.x LTS (`v3.13.2`) | v3.13.5 (21 Oct) on the LTS line; 3.15.1 newest | **v3.13.5**; CI promtool uses the same tag (tested) |
| Alertmanager | 0.34.1 | 0.34.1 still newest | **0.34.1** |
| Grafana | 13.2.x (≥ 13.2.2) | 13.2.4 (13 Oct) | **13.2.4** |
| Argo Rollouts | 1.10.0 | 1.10.1 (12 Oct; 1.9.2 and 1.8.5 same day) | **1.10.1** |
| Helm | 4.2.x | 4.3.1 (15 Oct); local 4.2.4 | **4.2.4** locally is fine; CI can take 4.3.1 |
| k3s | v1.36.x | v1.36.5+k3s1 (30 Sep); 1.37.1 newest | **v1.36.5+k3s1** (N-1 rule) |
| TimescaleDB image | `2.27.x-pg18` | `2.31.0-pg18` (20 Oct); the schema ran on both | **2.31.0-pg18** (Rev 2 says "2.27.x or newer") |
| Valkey | 9.1 | 9.1.2 | **9.1.2** |
| blackbox exporter | "pin-at-kickoff" | v0.29.1 (21 Oct) | **v0.29.1** |
| k6 | 2.2.x | 2.3.0 (21 Sep) | **2.3.0** |
| Sloth / Pyrra | 0.16.0 / 0.10.1 | 0.16.0 / 0.10.2 | **0.16.0** / 0.10.2 |
| kubeconform | `:latest` in `ci.yml` | v0.7.0 (tested) | **v0.7.0**, never `:latest` (fixed in the delivery reference) |
| kind / node image | — | kind v0.33.0 (26 Aug; Windows has v0.32.0); no `kindest/node:v1.36.5`, `v1.36.4` exists | **kind v0.33.0** in `Ubuntu-D`, node image **v1.36.4** (tested) |
| kubectl | — | 1.37.1 stable, 1.36.5 on the 1.36 line | **1.36.5** (N-1, matches k3s) |
| kube-prometheus-stack chart | matching Prometheus 3.13 | not checked | check the chart's `appVersion` |
| cosign, syft | current | not checked | check |
| GitHub custom deployment protection rules | public preview (Rev 2) | not checked | check availability and plan terms for public repositories |
| LLM prices | Rev 2 §E | not checked | update `config/llm.yaml` `pricing` |

### W0.2 Install the skills as folders (P-2)

```bash
cd ".claude/skills"
for f in *.skill; do python -I -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall('.')" "$f"; done
mkdir -p ../../docs/skills-dist && git mv *.skill *.SKILL.md budgetguard-skill-evals.zip BudgetGuard_*.md README.md ../../docs/skills-dist/
```

Exit: a new Claude Code session lists the eight `budgetguard-*` skills.

### W0.3 Project rules file (P-3)

`CLAUDE.md` at the root: the §0.1 precedence table, the mandates from §0.2, "re-check
versions against `.env.versions`", and "security changes update `security-and-api.md` in the
same PR".

### W0.4 Turn the Initializr zips into the reactor

1. `mkdir services` and extract the four zips into it (one folder per service).
2. Create `services/pom.xml` from `budgetguard-spring-services` §1. Set the Spring Boot parent
   to **4.1.2**, `<packaging>pom</packaging>`, the five `<module>` entries, and
   `resilience4j.version` from W0.1. Move the configuration-processor
   `annotationProcessorPaths` block into `<pluginManagement>` here so the services do not
   repeat it.
3. In each service `pom.xml`: replace the Spring Boot `<parent>` with
   `dev.budgetguard:services:1.0.0`; delete `<version>0.0.1-SNAPSHOT</version>` and the empty
   `name/description/url/licenses/developers/scm` blocks; add the `bg-common` dependency.
4. Create `services/bg-common/pom.xml` as a plain jar (no `spring-boot-maven-plugin`) with
   `spring-boot-starter-webmvc`, `spring-boot-starter-actuator`,
   `io.github.resilience4j:resilience4j-circuitbreaker`, `resilience4j-micrometer`.
5. Keep one Maven wrapper in `services/` (move one copy of `mvnw`, `mvnw.cmd`, `.mvn/`; delete
   the others) and apply P-1 to `maven-wrapper.properties`.
6. Delete the four zips from the repository root once `mvnw.cmd -q -DskipTests package` passes.

Exit: `cd services && mvnw.cmd verify` passes with the four generated context-load tests.

### W0.5 Repository hygiene

- `.gitignore`, `.env.example` and `CODEOWNERS` from the starter code §3. Extend CODEOWNERS
  with `/.github/workflows/` and `/deploy/` (security §11 #14).
- Branch protection on `main`: PR required, code-owner review, status checks `ci / *`.
- `gitleaks` pre-commit hook and CI step.
- `ci.yml` skeleton with the `java` job only; grow it every week.

### W0.6 Dev environment: Kubernetes toolchain inside `Ubuntu-D` (P-4)

**Decision: run every container and Kubernetes job inside the `Ubuntu-D` WSL 2 distro, next to
its Docker Engine. Keep the Windows `helm`, `kind`, `kubectl` and `make` installs untouched as
fallbacks.** This follows the setup guide you supplied, with the corrections below. Each
step was tried on this machine on 25 Oct 2026.

Why not Windows `kind`: it shells out to a `docker` CLI, Windows has none, and the engine in
`Ubuntu-D` listens only on its Unix socket. Bridging needs a Windows Docker CLI plus either an
unauthenticated `tcp://127.0.0.1:2375` listener (any local process gets root-equivalent access
to the engine) or an SSH docker context. Docker Desktop is not needed either.

**Where each job runs**

| Work | Runs on | Verified 25 Oct |
| --- | --- | --- |
| `mvnw.cmd verify`, platform unit tests (`uv run pytest`), dashboard `npm test` / `lint` / `build`, `helm lint` / `helm template` | Windows (fast editor loop; JDK 21, Python 3.14, Node 24 already there) | Yes: all of these ran on Windows today |
| `docker compose` (`make up`, demos), promtool, kubeconform, image builds, Sloth | `Ubuntu-D` | promtool, kubeconform and TimescaleDB ran today |
| testcontainers integration tests | `Ubuntu-D` (install uv there) | The SQL they wrap ran today |
| kind cluster, Argo Rollouts, chart installs, `kubectl argo rollouts` | `Ubuntu-D` | Cluster created and deleted today |
| `kubectl` / `helm` from Windows against that cluster (optional) | Windows, via the exported kubeconfig | Yes, while the distro is running (WSL forwards `127.0.0.1:<port>`) |

**Setup inside `Ubuntu-D` (tested; no sudo needed for these three)**

Corrections to the guide: pin versions instead of taking whatever is newest; install
`kubectl` 1.36.x instead of `snap install kubectl --classic` (the snap tracks 1.37, but this
project runs Kubernetes 1.36, the N-1 rule); kind **v0.33.0** is right (released 26 Aug, newer
than the Windows v0.32.0); verify checksums.

```bash
mkdir -p ~/.local/bin && cd /tmp            # ~/.local/bin is already on PATH in Ubuntu-D
curl -fsSLo kind https://kind.sigs.k8s.io/dl/v0.33.0/kind-linux-amd64
install -m 0755 kind ~/.local/bin/kind
curl -fsSLO https://dl.k8s.io/release/v1.36.5/bin/linux/amd64/kubectl
curl -fsSLO https://dl.k8s.io/release/v1.36.5/bin/linux/amd64/kubectl.sha256
echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c && install -m 0755 kubectl ~/.local/bin/kubectl
curl -fsSLO https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz
curl -fsSLO https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz.sha256sum
sha256sum -c helm-v4.3.1-linux-amd64.tar.gz.sha256sum && tar -xzf helm-v4.3.1-linux-amd64.tar.gz
install -m 0755 linux-amd64/helm ~/.local/bin/helm
sudo apt-get install -y make                # needs your password; not done by Claude
# k6: Grafana's apt repository, or run the grafana/k6:2.3.0 image
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Status today: `kind` v0.33.0, `kubectl` v1.36.5 and `helm` v4.3.1 are installed in
`~/.local/bin` in `Ubuntu-D`. `make`, `k6` and `uv` are not installed there yet.
Windows already has `make` (winget `ezwinports.make`), but Makefile targets that call
`docker` must run in WSL.

**Cluster for local tests** (tested with `kind create cluster --image kindest/node:v1.36.4`:
Ready in under 30 s; the node runs v1.36.4). There is no `kindest/node:v1.36.5` image, so pin
`v1.36.4` and let the node image, not the kind default (1.37), set the Kubernetes version.

```bash
kind create cluster --name bg --image kindest/node:v1.36.4 --wait 120s
kind get kubeconfig --name bg > /mnt/c/Users/RAJARSHI/.kube/kind-bg.yaml   # optional: Windows kubectl/helm
```

**Two WSL behaviours found while testing, and what the plan does about them**

1. **`Ubuntu-D` stops when no WSL session is attached**, and the Docker Engine and every kind
   container stop with it. Windows `kubectl` then gets "connection refused". During a working
   session, keep an `Ubuntu-D` terminal open (VS Code's WSL window counts). For long demo runs
   (k6 soak, demo mode), also look at the WSL idle-timeout settings in `%UserProfile%\.wslconfig`
   for your WSL version (2.7.8 here) and test them before relying on them.
2. **The kind node does not restart by itself after the distro stops.** It exits with code 128;
   that held with `--restart=unless-stopped` too. `docker start bg-control-plane` brings the
   cluster back healthy in about 25 s. Add a Makefile target:

   ```make
   cluster-up:            ## start (or restart) the local kind cluster after WSL was stopped
   	@docker start $$(docker ps -aq --filter label=io.x-k8s.kind.cluster=bg) >/dev/null 2>&1 \
   	  || kind create cluster --name bg --image kindest/node:v1.36.4 --wait 120s
   	@kubectl --context kind-bg wait --for=condition=Ready node --all --timeout=120s
   ```

**Paths and line endings**

- The repository stays on `D:\New Projects\Budget_Guard`; from `Ubuntu-D` it is
  `/mnt/d/New Projects/Budget_Guard` (quote it, it has a space). Bind mounts from `/mnt/*` work
  (today's checks used them) but builds over that bridge are slower. If compose image builds
  feel slow, keep a second clone inside the distro (`~/src/budgetguard`, which is also on D:,
  inside the distro's disk image).
- A script with CRLF endings failed under bash today. Add a root `.gitattributes` before the
  first code commit:

  ```text
  * text=auto eol=lf
  *.cmd text eol=crlf
  *.bat text eol=crlf
  *.ps1 text eol=crlf
  ```

- In Git Bash on Windows, arguments that look like paths are rewritten (`kubectl get --raw
  /readyz` turned into a Windows path and failed during testing). Use PowerShell, or prefix
  with `MSYS_NO_PATHCONV=1`, when running such commands from Windows.

**Images already pulled into `Ubuntu-D`** (reusable): `prom/prometheus:v3.13.5`,
`timescale/timescaledb:2.31.0-pg18` and `2.27.0-pg18`, `kindest/node:v1.36.4`,
`ghcr.io/yannh/kubeconform:v0.7.0`.

Exit: in an `Ubuntu-D` terminal, `docker compose version`, `kind version`, `kubectl version
--client` and `helm version` all print; `make cluster-up` gives a Ready node; with the
distro running, Windows `kubectl --kubeconfig %USERPROFILE%\.kube\kind-bg.yaml get nodes` works.

### W0.7 Optional: clean skill comparison

The earlier eval's "without skills" baseline could read the skill files in five of sixteen
cases. Re-run `trigger-evals/harness/functional.py` with the baseline in a directory that
cannot reach the skills (the report has the commands). This measures the skills; it does not
block the build.

**Week 0 exit:** reactor builds; skills load; versions pinned; `.gitattributes` in place; a kind
cluster in `Ubuntu-D` is reachable from Windows; CI runs the Java job.

---

## 7. Weeks 1–8

Each week lists goal, the skill to open first, work packages, tests, exit and risks. Code is
not repeated here: each WP points to the skill section that holds it.

### Week 1 — Services and metrics (M1)

Open first: `budgetguard-spring-services`.

| WP | Work | Files | Skill reference |
| --- | --- | --- | --- |
| W1.1 | `bg-common` metrics: `CommonTagsConfig` (service tag), `ErrorOriginObservationConvention`, `ErrorOriginAdvice` with the `ResponseStatusException` handler so a 404 is not a self-inflicted 500 | `bg-common/.../metrics/` | §2, §3 |
| W1.2 | `DownstreamClient`: 500 ms connect / 2 s read timeouts, breaker named after the dependency, 5xx/timeout → `DownstreamException`, 4xx pass through; breaker metrics bound; no stacked retries | `bg-common/.../http/` | §3 |
| W1.3 | Endpoints. **auth**: `POST /login`, `GET /validate` taking the token in `Authorization: Bearer` [new, security §11 #17], Resilience4j `RateLimiter` on `/login` returning 429 (Rev 1 idea, §3.2). **orders**: `POST /orders` → validate (auth) → reserve (inventory) → notify (notifications). **inventory**: `GET /stock/{sku}`, `POST /reserve`, in-memory stock with a stale-cache fallback. **notifications**: `POST /notify` → 202, in-memory queue, virtual-thread dispatcher, per-channel bulkheads, `notification.delivery` timer with a 2-minute SLO bucket | each service | §1, §5 |
| W1.4 | `application.yml` per service: virtual threads, graceful shutdown, exposure `health,info,prometheus`, probes, SLO buckets `300ms,500ms,800ms,2s`, fault admin settings, downstream base URLs (`bg.downstream.*-url`) | each service | §2 |
| W1.5 | Faults: `FaultFilter` (labels and answers with `sendError`), `FaultState` with `expiresAt`, `FaultAdminController` (both compiled, in `references/fault-admin.md`) (`GET/POST/DELETE /admin/faults`, constant-time token compare with `MessageDigest.isEqual`, 404 when the admin profile is off, `ttlSeconds` ≤ 3600 else 400), env flags `FAULT_ERROR_RATE`, `FAULT_LATENCY_MS`, `FAULT_DEPENDENCY`, `FAULT_CRASH` | `bg-common/.../faults/` | §4; security §4.1, T-7 |
| W1.6 | Dockerfiles (layered, non-root uid 10001, `MaxRAMPercentage=75`) | each service | §6 |
| W1.7 | Compose v1 (run in `Ubuntu-D`): four services + Prometheus (35 d retention) + blackbox; `deploy/compose/prometheus.yml` scrape config with blackbox relabelling; `load/steady.js` at 20 req/s | `deploy/compose/`, `load/` | slo-rules §5; spring §7 |

Tests (`mvnw verify`):

- `MetricsContractTest` per service: `service="<name>"`, `le="0.5"` (or the service's
  boundary), `error_origin="self"` after a throwing test controller. Re-check the Boot 4.1 name
  of `@AutoConfigureObservability`.
- orders: a 404 → `error_origin="none"`; auth 503 through `DownstreamClient` →
  `dep:auth-service` with status 502; open breaker → `dep:auth-service` with 503.
- notifications: a failed delivery is counted in `_count` but not in the `le="120.0"`
  delivered bucket.
- `/admin/faults`: no token → 401; profile off → 404; `ttlSeconds: 7200` → 400; expiry reverts.
- `/login` rate limit → 429, and the 429 does not appear as a 5xx.

**Exit:** `make up`, then in Prometheus every series the specs will read exists:
`http_server_requests_seconds_count{service=...}` for all four, `_bucket{le="0.3"|"0.5"|"0.8"}`,
`notification_delivery_seconds_bucket{le="120.0"}`, `probe_success` for four instances; with
`FAULT_DEPENDENCY=auth-service` on orders, `error_origin="dep:auth-service"` appears.

Risks: compose runs in `Ubuntu-D`, the Maven build on Windows (§6 W0.6); Boot 4 moved
packages (`MeterRegistryCustomizer` is now under `org.springframework.boot.micrometer.metrics.autoconfigure`,
already fixed in the skill); `FAULT_DEPENDENCY` and `FAULT_CRASH` handling still has to be written
(`references/fault-admin.md` says where).

### Week 2 — SLO-as-code (M2)

Open first: `budgetguard-slo-rules`.

| WP | Work | Files |
| --- | --- | --- |
| W2.1 | Four specs with the Rev 2 §2 SLOs. availability + latency for each; notifications latency is `latency_bucket` over `notification_delivery_seconds` with objective 0.95; `min_events_per_hour: 600`; `failure_mode: fail_open`; `probe` equal to the blackbox `instance` label (Rev 2 §4.2 used a different host and `probe_success{target=...}`; the label is `instance`) | `slo/*.yaml` |
| W2.2 | `rulegen/spec.py` (pydantic models), `rulegen/generate.py` (recording groups, alert groups, `bg:slo_allowed_ratio`, `increase30d`, `_RuleDumper`) | `platform/budgetguard/rulegen/` |
| W2.3 | `bg` CLI: `generate`, `--check`, `--format prometheus-operator`, `export sloth`, `sync-slos` (stub until W4) | `rulegen/cli.py` |
| W2.4 | promtool tests: a template generates one fast-burn test per service per tier, plus the hand-written edge cases (traffic floor, dead target with both integrity branches, `round()` for float compares) | `rules/tests/` |
| W2.5 | Python project scaffold: `pyproject.toml` from the starter code §4 (FastAPI 0.143.0), `uv lock`, ruff, mypy, pytest | `platform/` |
| W2.6 | CI: `python` job (ruff, mypy, pytest, `bg generate --check`), `rules` job (promtool from the same Prometheus tag) | `.github/workflows/ci.yml` |
| W2.7 | Mount `rules/generated/` into compose Prometheus; reload | `deploy/compose/` |

Tests: generator unit tests (every window an alert references has a recording rule; output is
byte-stable across runs; `le` written in float form), promtool tests above.

**Exit:** CI fails if anyone edits a generated rule; `promtool test rules` passes for every
service and tier; in Prometheus `bg:error_ratio:rate{5m,30m,1h,6h,3d}` and
`bg:self_error_ratio:rate*` exist for every SLO.

### Week 3 — Truthful SLIs and alerting (M3, M6 math, D-4, D-5 math)

Open first: `budgetguard-slo-math`, then `budgetguard-slo-rules` §5.

| WP | Work | Files |
| --- | --- | --- |
| W3.1 | `budget.py` (incl. `sum_counts`), `integrity.py` (`classify`, `sli_state`), `eta.py` with the zero-traffic guard and tz-aware check | `platform/budgetguard/` |
| W3.2 | Prometheus client with 3 s timeout, circuit breaker and a `SignalHealth` gatherer (`absent`, `min(up)`, `probe_success`, `increase(total[1h])`) | `integrations/prometheus.py` |
| W3.3 | Alertmanager config: route tree with `continue: true`, inhibition page→ticket on `[service, slo]`, Slack receivers via `api_url_file`, `budgetguard` webhook receiver with `credentials_file` | `deploy/compose/alertmanager.yml` |
| W3.4 | Blackbox module config and probes for the four readiness endpoints | `deploy/compose/blackbox.yml` |
| W3.5 | `SLIIntegrityLost` verified live; add `BudgetGuardBlind` (platform cannot reach Prometheus) and `PlatformWideBurn` (three or more services paging) alerts | generator + spec |
| W3.6 | Chaos check: `docker compose stop orders-service` → integrity STALE/NO_DATA → UNKNOWN | runbook `docs/runbooks/dead-service.md` |

Tests: all of slo-math §5 (ladder, 50 h, 43.2 min, negative remaining, request weighting,
dead service, low traffic, both ETA fixtures, no-traffic ETA). These tests are never marked
xfail.

**Exit:** stopping a service yields UNKNOWN in `sli_state()` from live data and fires
`SLIIntegrityLost` within about 3 minutes; the two ETA fixtures give 7 h 20 m and 27–28 days.

### Week 4 — History, platform skeleton, dashboards (M4, M7, security baseline)

Open first: `budgetguard-data-layer`, then `budgetguard-nextjs-dashboard`.

| WP | Work | Files |
| --- | --- | --- |
| W4.1 | Compose v2: TimescaleDB TSL image, Valkey with a password, a one-shot `migrate` service that runs Alembic (no initdb mount, A-18) | `deploy/compose/` |
| W4.2 | Migrations: `001_schema` (DDL from `schema-and-queries.md` §1 **plus** `decisions.github_deployment_id BIGINT UNIQUE` and `overrides.approver_id BIGINT` [new, security §11 #2, #7]), `002_timeseries` (autocommit block), `003_seed`, `004_roles` [new: `bg_app`, `bg_migrator`, `bg_grafana` read-only with view allowlist and 5 s timeout, `bg_auth` for Better Auth's schema], `005_eval_runs` | `platform/migrations/`, `platform/sql/` |
| W4.3 | `slo_registry.py` + `bg sync-slos` (upsert `slos` with `spec_hash`) | `platform/budgetguard/` |
| W4.4 | Snapshotter with the advisory-lock leader, aligned buckets, 6 h back-fill, explicit `integrity` per bucket | `workers/snapshotter.py` |
| W4.5 | Platform skeleton from the starter code §4: `config.py` (with the new fields: `github_installation_id`, `github_repo_id`, `github_owner_id`, `github_webhook_secret_previous`, `approver_ids`, `dashboard_token_next`, `alertmanager_token_next`), `store.py`, `pgstore.py`, `queue.py`, `auth.py`, `incidents.py`, `api.py`, `main.py`, plus `edge.py` (public-path guard) and a log-redaction filter | `platform/budgetguard/` |
| W4.6 | Read endpoints for the dashboard: `/v1/services/summary` (both all-errors and self-only remaining, burn 1h, integrity, ETA, last decision, 30 daily values), `/v1/decisions/page` (registered before `/{id}`), `/v1/decisions/{id}`, `/v1/policy/versions` | `api.py` |
| W4.7 | Dashboard: Next 16.3.8 app from `dashboard-code.md`; service cards (state as a sentence, negative budgets printed as negative, D-3 split line, demo-data label), CSS tokens with dark mode, CSP headers, standalone image | `dashboard/` |
| W4.8 | Grafana provisioning: TimescaleDB datasource as `bg_grafana`, Prometheus datasource; dashboard "BudgetGuard / Service reliability" with burn rate (5m/1h/6h), 30-day availability (`sli_hourly`), MTTR p50/p90 per incident type, deploy frequency and CFR, integrity minutes; anonymous Viewer, login form disabled | `grafana/` |
| W4.9 | Testcontainers integration test (run in `Ubuntu-D`, §6 W0.6) against `timescale/timescaledb:2.31.0-pg18`: migrations apply; snapshot upsert is idempotent; `bg_grafana` cannot read `auth.*` or `overrides.reason` | `platform/tests/integration/` |

Tests: the 14 starter-code API tests; `test_security.py` (the 28 cases from today, adapted to
the real modules); snapshotter unit tests with a fake Prometheus; dashboard Vitest (8) and
`next build` all Partial Prerender.

**Exit:** after 24 h of `steady.js`, the 30-day availability from `sli_buckets` equals
`bg:error_ratio:increase30d` within 0.01 percentage points for every SLO; the four Grafana
panels and the four dashboard cards show data.

Risks: continuous aggregate DDL inside a transaction (use the autocommit block); real-time
aggregation is off by default, so read the newest hour from `sli_buckets`.

### Week 5 — The gate (MVP complete: D-1, D-5 wired, D-8 records)

Open first: `budgetguard-admission-gate` (SKILL.md, `github-app.md`, `security-and-api.md`),
then `budgetguard-delivery-platform`.

| WP | Work | Files |
| --- | --- | --- |
| W5.1 | `gate.py` (chain A-06, `CONSERVATIVE`, `canary_profile()`, `digest()`), `pricing.py` with defaults (prior Beta(2,18), default failure cost 5%, size tiers). With no history the price is the prior; real features and history arrive in W6 | `platform/budgetguard/` |
| W5.2 | `lanes.py`: **rollback** (service subtree SHA via the git trees API vs a healthy earlier deploy) and **override** (query 4.10a with `FOR UPDATE SKIP LOCKED`). Incident-fix lane in W6 | `lanes.py` |
| W5.3 | `records.py`: `DeployFacts` → `GateInput`; store `inputs = {facts, gate_input, lane_evidence, github}` from the **first** decision so D-6 history is leak-free | `records.py` |
| W5.4 | `policy/freeze-policy.yaml` v1 (`attribution: charge_origin`, `self_page_share: 0.5`, `low_traffic_prices_normally: true`, pricing defaults); boot inserts the policy version row | `policy/` |
| W5.5 | GitHub App integration from `github-app.md` (size cap, dual secret, repo/installation check, `validate_job`, down-scoped tokens); route `deployment_status` and `pull_request` events to `bg:deploy-events` | `integrations/github_app.py` |
| W5.6 | Gate worker: the 7-step contract, `validate_job` first, skip on an existing `deployment_id`, single-flight, generation cache, `asyncio.TaskGroup` gathering, one transaction, callback with retry, `bg:briefs`, dead-letter stream; inline evaluation when Valkey is down; `bg:records-pending` buffer when PostgreSQL is down | `workers/gate_worker.py` |
| W5.7 | `GET /v1/decisions` with the full OIDC claim set (security §6.2); overrides with approver id, delete-on-failed-re-run; `POST /v1/decisions/{id}/replay` (writes nothing) | `api.py`, `auth.py`, `integrations/callbacks.py` |
| W5.8 | ETA wired: `eta.py` over the last 30 days of buckets for blocked services; ETA in the template comment, the card and the decision page; second number at half-budget pace | `api.py`, `briefs.py` (template only) |
| W5.9 | Redelivery reconciler (`GET /app/hook/deliveries`, redeliver failed protection-rule deliveries) and admin-bypass ingestion | `workers/reconcilers.py` |
| W5.10 | Public deploy: k3s on Oracle A1, namespaces, Argo Rollouts 1.10.1 + kubectl plugin, kube-prometheus-stack with 35 d retention and the Alertmanager tree, platform Helm chart (PDBs, NetworkPolicies incl. the new `bg-apps` policy), service Helm chart as a Rollout with replica-weighted steps from `canary.weights`/`canary.bake` and the AnalysisTemplate (traffic check + canary-vs-stable self-burn), deployer RBAC, Cloudflare Tunnel with the path regex, edge rate limits | `deploy/helm/`, `deploy/k3s/` |
| W5.11 | GitHub setup script: App (private) with Deployments RW, Actions RW, Contents R, Pull requests RW; environments `production-<service>` with `can_admins_bypass: false`, protected branches only, protection rule, `KUBECONFIG` environment secret; variables | `deploy/k3s/github-setup.sh` |
| W5.12 | Workflows: `build-push.yml` (multi-arch, SBOM, provenance, cosign keyless), `deploy.yml` (paths-filter, `max-parallel: 1`, decision read with `jq -e`, `cosign verify` with workflow SHA, `env:` passing, `kubectl argo rollouts status`); third-party actions pinned by commit SHA | `.github/workflows/` |
| W5.13 | Dashboard: decision list and detail, override form (Better Auth GitHub sign-in, `read:user`, 20-character reason, server action, 403/409 copy), demo-data label | `dashboard/` |

Tests: gate unit tests (every row of the §D worked example, determinism and digest
stability, rollback with STALE + fail-closed → admit, override with NO_DATA + fail-closed +
page → admit, override single use, LOW_TRAFFIC both ways); webhook and worker security
tests; OIDC claim tests; override tests; one integration test that runs webhook → worker →
fake GitHub callback against TimescaleDB and Valkey containers.

**Exit (MVP):** on the public URL, `make demo-fast-burn` on orders → page within minutes →
merge a 900-line change with a migration → the run shows "Waiting", then rejected, with price,
affordable and ETA → trigger a rollback → admitted at price 0 → the decision page's Recompute
shows the same hash and verdict.

Risks: Oracle ARM capacity (start in week 1); the protection-rule callback shape (validate
against a captured payload, `validate_job` returns 422 on a new shape so it is visible).

### Week 6 — Pricing and attribution (D-2, D-3)

Open first: `budgetguard-admission-gate` §3–§5.

| WP | Work | Files |
| --- | --- | --- |
| W6.1 | `changes.py`: PR for the SHA, paginated files under `services/<service>/`, `lines_changed`, `touches_migration`, `config_only`, `dependency_bump`; no PR → `size_max` and say so in the reason | `changes.py` |
| W6.2 | History inputs: `prior_deploys`, `prior_failures` with the leak rule (`finished_at + 60 min ≤ t`), median `change_failure_budget_pct` | `records.py`, SQL 4.x |
| W6.3 | Deploy-events worker: deployments row from `deployment_status`, lead time, the 60-minute timer that marks a change failure only on self-inflicted burn and stores `change_failure_budget_pct` | `workers/deploy_events.py` |
| W6.4 | Incident-fix lane (open incident on this service **and** its `alertname` firing in `ALERTS`, PR ≤ 400 lines, `Fixes BG-INC-<id>`); security lane (Dependabot/Renovate + advisory) treated as incident-fix | `lanes.py` |
| W6.5 | `charge_origin` inputs: `active_page` requires `self_error_ratio_1h / error_ratio_1h ≥ self_page_share`; `remaining_pct` from `self_bad`; `attribution` stored in inputs | `records.py` |
| W6.6 | Chargeback: query 4.1 on the cards and in Grafana; freeze-impact view (freeze minutes, blocked deploys) [Rev 1 idea, §3.2] | SQL, dashboard, Grafana |

Tests: worked-example parametrized test fed from fixture rows (n = 30, f = 3, median 4.2%) to
prove the numbers come from data, not constants; m = 1.5 × 2.0 = 3.0 derivation; incident-fix
lane denied without a firing `ALERTS` series; dependency outage → orders `not BLOCK`, auth
`BLOCK`.

**Exit:** the §D table reproduces from real rows; `make demo-dependency-outage` freezes auth
and not orders, and the orders card says how much of its spent budget was self-inflicted.

### Week 7 — Backtester, Freeze Brief, evals (D-6, §E)

Open first: `budgetguard-mlops-evals` and its `eval-harness.md`.

| WP | Work | Files |
| --- | --- | --- |
| W7.1 | `backtest/` from the reference: `DeployFacts`, `to_gate_input(facts, policy)`, history export from `decisions.inputs.facts`, scoring (precision, recall, harm recall, healthy blocked, burn prevented, rollback admit rate), `gate_ci` | `platform/backtest/` |
| W7.2 | Synthetic library as cases with `must_be`/`must_not` and invariant/anchor strength (table in the mlops skill §2), plus the seeded population (4 × 150 deploys, a "true" risk model that differs from the policy) | `backtest/scenarios.py` |
| W7.3 | `policy-backtest.yml`: two checkouts (main code + main policy vs PR code + PR policy), compare, PR comment, fail on regression; publish to `policy_versions.backtest_report` on merge | `.github/workflows/` |
| W7.4 | Brief writer: `brief_view()` (no free text from people), template, provider adapters (OpenAI Responses, Anthropic Messages, Ollama), `config/llm.yaml`, prompt SHA, 4 s timeout, monthly budget breaker in Valkey, circuit breaker, online grounding | `briefs.py`, `integrations/llm.py`, `workers/brief_writer.py` |
| W7.5 | Grounding eval: 120-case golden set with adversarial slices, runner measuring the raw model, Wilson bound in the report, `eval_runs`; `brief-eval.yml` on prompt/config changes and nightly for the challenger | `platform/evals/` |
| W7.6 | Delivery: Slack (`#bg-briefs`) and a PR comment; the "budget recovered" template variant [Rev 1 idea] | `integrations/slack.py` |

Tests: the 34 harness tests from the reference; a test that a weight change produces a
different backtest; template scores 100% on the golden set.

**Exit:** a PR that lowers the reserve to 3% gets a backtest table and is blocked (the Rev 2
demo step 7); `brief-eval.yml` passes for the template and records a row for the default model.

### Week 8 — Stretch and polish (D-7, D-8, hardening, README)

Open first: `budgetguard-delivery-platform`, then the dashboard skill.

| WP | Work | Files |
| --- | --- | --- |
| W8.1 | D-7: Traefik traffic routing in the Rollout (`traefik.io` TraefikService), internal entrypoint `*.bg-apps.internal`, services' base URLs switched; measure that a 5% step carries 4–6% of requests | `deploy/helm/service/` |
| W8.2 | D-7: profiles `[25,100]` / `[10,50,100]` / `[5,25,50,100]` / `[100]` with bakes 2m/5m/10m/0s; `initialDelay: 2m`; `or vector(0)` numerators; `clamp_min` | `deploy/helm/service/` |
| W8.3 | D-8: Sloth parity (`bg export sloth` → `sloth generate` → promtool comparison at 5m/30m/1h/6h); OpenSLO import for ratio SLOs | `rules/parity/`, `rulegen/sloth.py` |
| W8.4 | D-8: decision explorer "Recompute" button and Playwright flows (unknown card explains itself, recompute says "Same verdict", short reason alert, override only on overridable blocks) | `dashboard/` |
| W8.5 | Security hardening: environment drift and cluster digest drift reconcilers, CI lint (only the environment job reads `KUBECONFIG`; actions pinned), Prometheus query limits, secret rotation drill for one secret | `workers/reconcilers.py`, `ci.yml` |
| W8.6 | Chaos drill of BudgetGuard (delivery reference §9), with the corrected redelivery row: kill both API pods → GitHub waits → the reconciler redelivers → one decision row | `docs/runbooks/chaos-drill.md` |
| W8.7 | Scaling decision evidence: k6 merge storm, p95 verdict latency for option A (raw 30 d PromQL) vs C (buckets); put both measured numbers in the README | `load/merge-storm.js` |
| W8.8 | README (Rev 2 §11.3 blueprint), architecture diagram export, screenshots, 3-minute demo GIF/video, decisions log including every row of §3 and §14 | `README.md`, `docs/` |

**Exit:** README checklist complete; chaos drill rows all show the expected result; D-7 weight
measurement recorded.

---

## 8. Platform API surface

The full contract lives in `security-and-api.md` §3. Summary for planning:

| Method and path | Caller | Auth | Public | Week |
| --- | --- | --- | --- | --- |
| `POST /v1/github/webhook` | GitHub | HMAC (dual secret), repo + installation | yes | W5 |
| `GET /v1/decisions?service&run_id` | deploy job | OIDC (repo, ids, environment, run, ref, workflow) | yes | W5 |
| `POST /v1/webhooks/alertmanager` | Alertmanager | bearer | no | W4 |
| `GET /v1/services/summary` | dashboard | service bearer | no | W4 |
| `GET /v1/decisions/page` | dashboard | service bearer | no | W4 |
| `GET /v1/decisions/{id}` | dashboard | service bearer | no | W4 |
| `POST /v1/decisions/{id}/replay` | dashboard | service bearer | no | W5 |
| `GET /v1/policy/versions` | dashboard | service bearer | no | W4 |
| `POST /v1/overrides` | dashboard for a signed-in human | service bearer + user GitHub token → approver id | no | W5 |
| `/livez`, `/readyz`, `/metrics` | kubelet, Prometheus | none | no | W4 |

---

## 9. Testing strategy

| Layer | Tool | What it proves | Runs |
| --- | --- | --- | --- |
| Math | pytest | Ladder, 50 h, 43.2 min, negative remaining, request weighting, ETA fixtures | every PR |
| Rules | promtool v3.13.5 (same tag as the server) | Every window and tier fires or stays quiet as expected | every PR |
| Generator | pytest | Byte-stable output; `--check` catches hand edits | every PR |
| Services | JUnit + Spring test slices | Metric contract, `error_origin` cases, fault endpoint, rate limit | every PR |
| Gate | pytest | Worked example, chain order, lanes, determinism | every PR |
| Security | pytest | `security-and-api.md` §9 list | every PR |
| Integration | testcontainers (TimescaleDB 2.31, Valkey 9.1) in `Ubuntu-D` locally, on the runner in CI | Migrations, every reference query, snapshotter idempotency, webhook→worker→callback, override consumed once | every PR (from W4) |
| Policy | backtest | No invariant broken, no metric regressed > 2 pp | policy PRs |
| Brief | grounding eval | ≥ 99% grounding, ≥ 95% fact recall, p95 ≤ 4 s, 0 forbidden | prompt/config PRs, nightly |
| Dashboard | ESLint 9, `tsc`, Vitest, Playwright, `next build` | Lint clean, contract parsing, flows, all routes Partial Prerender | every PR |
| Charts | `helm lint`, `helm template` + kubeconform v0.7.0 with the CRD catalog | Charts render and validate (verified 25 Oct: 11/11 objects valid) | every PR |
| End to end | demo script on the public URL | The Rev 2 3-minute demo | end of W5, W8 |

---

## 10. Security workstream

Every item from `security-and-api.md` §11, scheduled:

| # | Control | Week |
| --- | --- | --- |
| 1 | Webhook repo/installation check; App private | W5 |
| 2 | Second dedupe on `deployment.id` (`decisions.github_deployment_id UNIQUE`) | W4 (column), W5 (worker) |
| 3 | Dual webhook secret | W5 |
| 4 | `validate_job` before any token is minted | W5 |
| 5 | Down-scoped installation tokens | W5 |
| 6 | Full OIDC claim set | W5 |
| 7 | Approvers by numeric id | W4 (config, column), W5 (API) |
| 8 | Override deleted when the re-run fails | W5 |
| 9 | Incident-fix lane needs a firing `ALERTS` series | W6 |
| 10 | Public-path guard | W4 |
| 11 | Redelivery reconciler | W5 |
| 12 | Environment and cluster drift reconcilers | W8 |
| 13 | `cosign verify --certificate-github-workflow-sha` | W5 |
| 14 | CODEOWNERS on workflows and deploy; actions pinned by SHA | W0 (CODEOWNERS), W5 (pins) |
| 15 | `bg_grafana` read-only role; Prometheus query limits | W4 (role), W8 (limits) |
| 16 | `bg-apps` NetworkPolicy; Valkey password | W4 (password), W5 (policy) |
| 17 | `/validate` token in a header | W1 |
| 18 | Dual dashboard and Alertmanager tokens | W4 |

Secrets are created per `security-and-api.md` §7; the first rotation drill (one secret, end to
end) is part of W8.5.

---

## 11. BudgetGuard's own reliability

| Item | Setting | Week |
| --- | --- | --- |
| Meta-SLO | 99.9% of synthetic protection-rule webhooks (blackbox, every minute) get a verdict within 5 s over 30 days | W5 (probe), W8 (panel) |
| Replicas | platform-api 2 + PDB `minAvailable: 1`; gate-worker 2; snapshotter 2 (advisory lock); dashboard 2; brief-writer 1 | W5 |
| Rollout | `maxUnavailable: 0`, `maxSurge: 1` | W5 |
| Bulkheads | separate pools for verdicts, briefs, deploy events; dashboard cache never shared with the gate | W5 |
| Timeouts | Prometheus 3 s, GitHub 10 s, Slack 5 s, LLM 4 s, service-to-service 2 s | W1–W7 |
| Backups | nightly `pg_dump -Fc` + WAL archiving; monthly restore drill | W5 (job), W8 (drill) |
| Failure modes | Rev 2 §8 mitigation table, with the redelivery correction | W8 drill |

---

## 12. Risk register

| Risk | Likelihood | Impact | Mitigation | Owner week |
| --- | --- | --- | --- | --- |
| Windows tools cannot reach the WSL Docker Engine | Certain today | Windows `kind`, testcontainers from Windows | Run container work in `Ubuntu-D` (§6 W0.6) | W0 |
| `Ubuntu-D` stops when no session is attached, taking Docker and kind down | Confirmed 25 Oct | Demo runs and soak tests die silently | Keep a WSL session open; `make cluster-up` restarts the kind node; test the WSL idle settings before demo mode | W0, W5 |
| CRLF line endings break scripts under bash | High on Windows | Makefile, shell scripts, compose entrypoints fail in WSL | Root `.gitattributes` with `eol=lf` (§6 W0.6) | W0 |
| A pinned version does not exist (Rev 2's Resilience4j 3.x) | Happened | Build fails at W0.4 | Pins come from the 25 Oct check table (§6 W0.1), re-checked on the start day | W0 |
| Oracle A1 capacity unavailable | Medium | Public link slips | Sign up in W1; fallback: a paid 8 GB ARM VM, or kind on a laptop for the demo video | W1 |
| GitHub feature terms change (custom protection rules) | Low | D-1 design | Re-check in W0.1; the gate API also works with a `workflow_run`-level check as a degraded mode (never the primary) | W0 |
| Free VM memory over 8 GB | Medium | Pods evicted | Measure RSS under the merge storm; drop brief-writer and Grafana replicas first | W5, W8 |
| Version churn (FastAPI, Next.js) | High | Build breaks | Pins in lockfiles; upgrade only via PR with CI | all |
| Webhook payload shape differs from docs | Low | Gate ignores deploys | Capture a real payload in W5; `validate_job` 422 makes it visible | W5 |
| Pricing priors questioned in interviews | Certain | Credibility | D-6 backtest table; say "priors, backtested" | W7 |
| LLM provider outage or price change | Medium | Brief quality | Template fallback scores 100%; model switch is a config PR | W7 |
| Over-scoping week 8 | Medium | Polish suffers | D-7 and Sloth parity are stretch; README and demo are not | W8 |

---

## 13. Differentiator implementation details

### 13.1 D-1 — Unbypassable admission

- **Problem:** a `curl` step in YAML can be deleted (B-6), and its endpoints were
  unauthenticated (B-8).
- **Mechanism:** GitHub App as a custom deployment protection rule on each
  `production-<service>` environment; `KUBECONFIG` exists only as an environment secret; admin
  bypass off and reconciled; Argo Rollouts analysis as the in-cluster backstop; the
  drift reconcilers make any bypass visible.
- **Files:** `integrations/github_app.py`, `workers/gate_worker.py`,
  `workers/reconcilers.py`, `deploy/k3s/github-setup.sh`, `.github/workflows/deploy.yml`,
  `deploy/helm/service/templates/analysis.yaml`.
- **Data:** `decisions` (one row per GitHub request), `deployments`, `overrides`.
- **Tests:** webhook security tests; CI lint that only the environment job reads
  `KUBECONFIG`; integration test webhook → callback.
- **Demo:** delete the `environment:` line in a branch: the job has no credentials; the
  protection rule still runs on `main`.
- **Interview line:** "Delete the line and you lose the keys."

### 13.2 D-2 — Deploy pricing

- **Formula:** `price = (α₀ + f)/(α₀ + β₀ + n) × m_risk × c̃_fail`; admit if
  `price ≤ k × (B_remaining − B_reserve)`, with Beta(2,18), k = 0.5, reserve 5%.
- **Inputs:** `changes.py` features; history from `deployments` with the leak rule; median
  failure cost from `change_failure_budget_pct` (5% until history exists).
- **Week 5** ships the engine with defaults; **week 6** feeds real features and history.
- **Tests:** every row of the worked example from fixture rows; the m = 3.0 derivation.
- **Demo:** at 7% remaining, a 40-line change ships and a 900-line migration is blocked with
  "split the migration, or wait for the ETA".

### 13.3 D-3 — Culprit, not victim

- **Signal:** `error_origin` ∈ {`none`, `self`, `dep:auth-service`, `dep:inventory-service`,
  `dep:notifications-service`} on `http.server.requests` (W1).
- **Gate:** `attribution: charge_origin` uses self-only remaining and a self-share test on
  the page (W6); the canary analysis counts only `error_origin="self"` (W5/W8); change
  failures count only self-inflicted burn (W6).
- **Display:** all-errors and self-only budgets side by side; "N% of spent budget is
  self-inflicted".
- **Tests:** dependency-outage scenario is an invariant in the backtest; the Spring tests for
  each origin.

### 13.4 D-4 — Truthful SLIs

- **Signal checks:** `absent()`, `up == 0`, `probe_success == 0`, events below
  `min_events_per_hour` → `NO_DATA`/`STALE`/`LOW_TRAFFIC`.
- **Verdict:** HEALTHY / AT_RISK / UNKNOWN; UNKNOWN applies the service's `failure_mode`
  (fail-open admits as `admit_unverified` with a page; fail-closed blocks; a rollback always
  passes first).
- **Alert:** `SLIIntegrityLost` with `for: 2m`.
- **Tests:** dead-target promtool case; `classify()` and `sli_state()` unit tests; chaos check
  in W3.

### 13.5 D-5 — Exact unfreeze ETA

- **Algorithm:** replay the stored 5-minute buckets forward, dropping the oldest and appending
  projected buckets at recent traffic and an assumed future error ratio; O(n) over 8,640
  buckets; `None` when there is no recent traffic or no recovery within 30 days.
- **Presentation:** absolute UTC on the server, local relative time in the browser, always
  with its assumption ("if the error ratio stays at 0"), plus the half-budget-pace variant.
- **Tests:** the 7 h 20 m and 27–28 day fixtures; zero traffic → `None`.

### 13.6 D-6 — Policy backtesting in CI

- **Cases:** recorded `DeployFacts` (from W5) + synthetic library + seeded population.
- **Scores:** precision, recall, harm recall, healthy blocked, burn prevented, rollback admit
  rate (must be 1.0).
- **Gate:** invariant violations or a > 2 pp drop or > 20% more healthy blocks fail the PR;
  anchor moves warn.
- **Tests:** weight change produces a diff; leak rules; `gate_ci` cases.

### 13.7 D-7 — Budget-proportional canary

- **Mechanism:** profile from remaining budget (`[25,100]` ≥ 50%, `[10,50,100]` 20–50%,
  `[5,25,50,100]` < 20% or LOW_TRAFFIC or fix lane or unknown, `[100]` rollback); Traefik
  weighted routing including internal calls; analysis on canary vs stable self-burn.
- **Fallback:** without Traefik routing, the README says "replica-weighted canary".
- **Evidence:** measured share of requests at the 5% step.

### 13.8 D-8 — Interop and replayable decisions

- **Records:** `inputs_hash` = SHA-256 of the canonical `GateInput`; policy version; verdict;
  everything needed to recompute (W5).
- **Replay:** `POST /v1/decisions/{id}/replay` runs the same `evaluate()` and reports
  `matches` (W5 API, W8 UI).
- **Interop:** `bg export sloth` and the promtool parity test; OpenSLO import for ratio SLOs
  (W8).

---

## 14. Corrections to feed back into Rev 2

Rev 2 stays the source of truth, so these should be written into it (or into a Rev 2.1
errata section) to keep plan and code aligned:

1. Chain-order prose (§3 Flow 1 step 4) disagrees with the code (§4.11); the code is right,
   and an override lane follows the rollback lane.
2. D-3 is not wired into Rev 2's gate inputs; add `attribution: charge_origin`.
3. Overrides cannot post to a callback GitHub already consumed; override = record + re-run.
4. `LOW_TRAFFIC` should not short-circuit like `STALE`.
5. Lanes must be proven, and the incident-fix lane must check `ALERTS`, not only the
   incident table.
6. Backtest cases must store facts, not a priced `GateInput`.
7. The grounding check (§10.5) is too lenient and too strict at once.
8. The 5% canary is 25% on 4 replicas; internal calls bypass the ingress router.
9. `helm --wait` does not wait for canary analysis.
10. `/v1/decisions` must be public (OIDC-protected); signatures must be verified at deploy.
11. `incidents` must be unique on `(fingerprint, started_at)`.
12. Versions: FastAPI 0.143.0, Next.js 16.3.8, Better Auth; Spring Boot 4.1.2 from Initializr.
13. Java package `com.budgetguard.*` in §4.4–4.5 should be `dev.budgetguard.*`.
14. §4.2 `probe` host and §4.7 `probe_success{target=...}` should use the blackbox `instance`
    label and the cluster DNS name.
15. §9.2 compose mounts `platform/sql` into initdb while Alembic also owns the schema.
16. §9.4 snapshotter Lease → advisory lock.
17. §4.13 job lacks `run_id`; Flow 1 says the key is `(run_id, environment, service)` but the
    code keys on the delivery id. Both are now carried, plus `deployment_id`.
18. §11.1 uses `bg:slo_allowed_ratio`, which nothing defines (the generator now emits it).
19. §10.2 reads `.bake`, which the gate never produced (the API derives it).
20. The delivery chaos drill assumes GitHub redelivers failed webhooks; it does not, so a
    redelivery reconciler is required.
21. Overrides by "platform-lead role" → numeric GitHub user ids in `approver_ids`.
22. The Initializr wrapper uses Maven 3.10.0 while §C pins 3.9.x (P-1).
23. §C pins Resilience4j "3.x", which does not exist; use 2.4.0.
24. §5 query 4 (chargeback) calls `round()` on `DOUBLE PRECISION`; PostgreSQL needs a cast to
    `numeric`. Found by running it on TimescaleDB 2.27.0 and 2.31.0.
25. §4.3 relies on `management.metrics.tags.*`; Boot 4 also moved `MeterRegistryCustomizer`
    to `org.springframework.boot.micrometer.metrics.autoconfigure`. The skill's bean approach
    with the new import compiles.
26. §C's newer patch releases: Prometheus 3.13.5, Grafana 13.2.4, Argo Rollouts 1.10.1, k3s
    1.36.5, TimescaleDB 2.31.0, Valkey 9.1.2, blackbox 0.29.1, k6 2.3.0 (§6 W0.1).

---

## 15. Traceability to the Rev 2 audit grid

| # | Requirement | Where in this plan | Verified by |
| --- | --- | --- | --- |
| 1 | Four microservices | W1 | MetricsContractTest; compose up |
| 2 | SLIs and SLOs per service | W1.4, W2.1 | promtool; latency buckets exist |
| 3 | Grafana burn rate per service | W4.8 | panel shows data |
| 4 | 30-day rolling availability | W4.8 | matches Prometheus within 0.01 pp |
| 5 | MTTR per incident type | W4.8, SQL query 2 | median and p90 per type |
| 6 | Deployment change rate | W4.8, W6.3 | frequency, CFR, lead time, recovery time |
| 7 | Freeze via GitHub Actions | W5 | rejected run with comment |
| 8 | Prometheus, Alertmanager, Kubernetes | W1–W5 | pinned versions running |
| 9 | Exact versions | W0.1, §3.1 | `.env.versions`, lockfiles |
| 10 | MVP blueprint | §2.1, W1–W5 | MVP exit |
| 11 | HLD/LLD/architecture | §4, §5, Rev 2 §3–§4 | README diagram |
| 12 | DB choice | data-layer skill §1 | README decisions log |
| 13 | Cache/queue choice | data-layer skill §1 | README decisions log |
| 14 | Design patterns | Rev 2 §7 (chain, spec, template method, strategy, adapter, decorator, value object, idempotent receiver, outbox-lite, single-flight, leader election, breaker/bulkhead) | code review checklist |
| 15 | Docker and Kubernetes | W1.6, W4.1, W5.10 | images by digest; k3s running |
| 16 | DevOps/MLOps wrapper | W7 | backtest and brief eval gate CI |
| 17 | Resilience patterns | W1.2, §11 | breaker metrics; chaos drill |
| 18 | Mitigation strategies | §11, W8.6 | chaos drill rows |
| 19 | Availability and consistency | Rev 2 §8 per data path; W5.6 one transaction | integration test |
| 20 | Deployment strategies | W5.10, W8.1–8.2 | canary abort demo |
| 21 | Interview prep | Rev 2 §12 + skill soundbites | every number derivable |
| 22 | README | W8.8 | checklist |
| 23 | CI/CD to Docker Hub and a VM | W5.12 | signed images; deploy job |
| 24 | docker-compose | W1.7, W4.1 | `make up` |
| 25 | Monitoring dashboard | W4.8, §11 | meta-SLO panel |
| 26 | Scaling decision | W8.7 | measured numbers in README |
| 27 | Public live link | W5.10 | URL with demo mode |
| 28 | Differentiators | §2.2, §13 | per-differentiator exit |
| 29 | GPT-5 question | W7.4–7.5, Rev 2 §E | eval-gated model choice |

---

## 16. First ten actions

1. Decide P-1 to P-9 (§3.3).
2. Finish `Ubuntu-D` setup: `sudo apt-get install make`, k6, uv (kind, kubectl, helm are installed); root `.gitattributes`; `make cluster-up` (W0.6).
3. Install the skills as folders and add `CLAUDE.md` (W0.2, W0.3).
4. Re-check versions and write `.env.versions` (W0.1).
5. Build the Maven reactor from the Initializr zips (W0.4).
6. Add `.gitignore`, `.env.example`, CODEOWNERS with workflows and deploy, branch protection,
   gitleaks (W0.5).
7. Start the Oracle Cloud sign-up and pick the tunnel domain (P-5).
8. Implement `bg-common` (W1.1, W1.2, W1.5) with its tests.
9. Implement the four services' endpoints and `application.yml` (W1.3, W1.4).
10. Bring up compose v1 in `Ubuntu-D` with Prometheus and blackbox, and check the week 1 exit list (W1.7).
