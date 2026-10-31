# BudgetGuard — decisions log

The full register is in `BudgetGuard_Rev2_Detailed_Implementation_Plan.md` §3. This file records
what was actually pinned or decided while building, with the date, so the README's decisions
log can be written from it.

## Version pins (W0.1, checked 25 Oct 2026)

| Component | Pin | Where it is set |
| --- | --- | --- |
| Java | 21 (Temurin in CI, Oracle 21.0.11 locally) | `services/pom.xml`, `ci.yml` |
| Spring Boot | 4.1.2 | `services/pom.xml` |
| Resilience4j | 2.4.0 (Rev 2's "3.x" does not exist) | `services/pom.xml` |
| Maven | 3.9.16 via the wrapper (P-1) | `services/.mvn/wrapper/maven-wrapper.properties` |
| Prometheus | v3.13.5 (LTS line) | `deploy/compose/.env.versions` |
| Alertmanager | v0.34.1 | `deploy/compose/.env.versions` |
| Grafana | 13.2.4 | `deploy/compose/.env.versions` |
| TimescaleDB | 2.31.0-pg18 (TSL) | `deploy/compose/.env.versions` |
| Valkey | 9.1.2 | `deploy/compose/.env.versions` |
| blackbox exporter | v0.29.1 | `deploy/compose/.env.versions` |
| k6 | 2.3.0 | `deploy/compose/.env.versions` |
| gitleaks | v8.30.1 | `.pre-commit-config.yaml`, `ci.yml` |
| FastAPI | 0.143.0 (W2) | `platform/pyproject.toml` |
| Next.js / Better Auth | 16.3.8 / 1.7.12 (W4) | `dashboard/package.json` |
| Argo Rollouts / k3s / kind node | 1.10.1 / v1.36.5+k3s1 / v1.36.4 (W5, local) | charts, bootstrap |
| kube-prometheus-stack / cloudflared / cosign | 92.10.1 / 2026.10.1 / v3.1.3 via cosign-installer v4.1.2 (W5) | `deploy/k3s/bootstrap.sh`, workflows |

Not yet checked: syft (BuildKit's SBOM generator is used instead), LLM prices, GitHub custom
deployment protection rule terms.

## Decisions taken while building

| Date | Decision | Why |
| --- | --- | --- |
| 25 Oct 2026 | Maven wrapper pinned to 3.9.16 (P-1) | Matches Rev 2's 3.9.x pin and the local Maven |
| 25 Oct 2026 | Container and Kubernetes tooling runs in WSL 2 `Ubuntu-D` (P-4) | Docker Engine lives there; Windows `kind` cannot reach it |
| 25 Oct 2026 | Branch protection on `main` blocked: private repo on the Free plan (P-6 open) | GitHub requires Pro or a public repository |
| 26 Oct 2026 | bg-common is wired by Spring Boot auto-configuration, not component scanning | The services scan `dev.budgetguard.<service>`, which does not include `dev.budgetguard.common` |
| 26 Oct 2026 | Framework errors (400 validation, 404 unknown path, 405) keep their status and count as `error_origin="none"` | The skill's catch-all `Exception` handler turned them into self-inflicted 500s |
| 26 Oct 2026 | Injected faults are recorded with `uri="UNKNOWN"` | The fault filter answers before handler mapping; the SLO rules still count them and cardinality stays bounded |
| 26 Oct 2026 | auth `/validate` reads `Authorization: Bearer`; `/login` rate limited to 50/s with 429 | Token never in a query string (security §11 #17); a 4xx limit does not burn availability budget |
| 26 Oct 2026 | Orders translates expected dependency 4xx (401, 404, 409); an unexpected 4xx is charged to the dependency | Keeps `self` for orders' own faults only (D-3) |
| 26 Oct 2026 | Notifications delivery timer also carries a `channel` tag (3 values) | Per-channel bulkheads are visible; the SLI sums over it |
| 26 Oct 2026 | Inventory reservations never use the stale-stock cache | Selling stock we may not have is worse than a 5xx |
| 26 Oct 2026 | Error-ratio numerators zero-fill while traffic exists (`sum(rate(bad)) or 0 * sum(rate(total))`) | Without it a healthy service's ratio is empty, i.e. "no data"/UNKNOWN; no traffic still stays empty (D-4) |
| 26 Oct 2026 | `SLIIntegrityLost` matches probes by a `service` label from relabelling, not by URL | The same committed rules run in compose and k3s, where probe URLs differ |
| 26 Oct 2026 | PodMonitors use `jobLabel: app` (W5) | Generated `up{job="<service>"}` must match under kube-prometheus-stack |
| 26 Oct 2026 | promtool tests are generated per service and severity (`bg tests`) and checked in CI like the rules | Every window and tier an alert uses has a test; 1.6x/0.4x burns avoid .5 rounding boundaries |
| 26 Oct 2026 | Spec models forbid unknown keys and reject `le` not in float form and burns needing > 100% errors | Typos and Prometheus 3 `le` normalisation fail loudly at generate time |
| 26 Oct 2026 | Alertmanager routes tickets explicitly to `slack-tickets` after `budgetguard` | The root receiver only catches unmatched alerts, so tickets never reached a human (found with `amtool config routes test`) |
| 26 Oct 2026 | Platform alerts `PlatformWideBurn` (≥ 3 services paging), `BudgetGuardBlind` (`bg_prometheus_reachable == 0` for 1m), `Watchdog` heartbeat | Rev 2 §8 mitigation table; Watchdog lets an external dead man's switch notice Prometheus/Alertmanager death |
| 26 Oct 2026 | Platform's Prometheus client: 3 s timeout, breaker opens after 3 failures, 30 s half-open; unreachable → STALE, never healthy | D-4; the breaker state becomes the `bg_prometheus_reachable` gauge in W4 |
| 26 Oct 2026 | Compose secrets live in git-ignored `deploy/compose/secrets/`, made from `secrets.example/` by `make secrets`; Slack placeholders point at 127.0.0.1:9 | Nothing leaves the machine until real webhook URLs are set |
| 26 Oct 2026 | `astral-sh/setup-uv@v10.3.0` (reference had v6, last released Sep 2025) | Exact tag: setup-uv publishes no moving `v10` tag (CI failed to resolve it); newest release older than two weeks; SHA pins follow in W5 |
| 26 Oct 2026 | Migrations run SQL files through psycopg's raw cursor (sync Alembic on psycopg 3); `002` is split per statement in an autocommit block | asyncpg prepares every statement (no multi-statement files); `exec_driver_sql` passes an empty tuple, so a `%H` in a comment broke `003`; continuous aggregates cannot be created in a transaction |
| 26 Oct 2026 | Migration `004` grants LOGIN with a password only for roles whose `BG_DB_<ROLE>_PASSWORD` is set, quoted by PostgreSQL's `format(%L)` | No password in SQL or code; an unset role stays NOLOGIN |
| 26 Oct 2026 | Better Auth tables are an Alembic migration (`006`, run as `bg_auth` in schema `auth`), SQL compiled by `dashboard/scripts/auth-schema.mjs` with better-auth's own planner | One schema owner; the CLI moved to a separate `auth` package in 1.7, and the dashboard image has no CLI; tables owned by `bg_auth`, unreadable by every BudgetGuard role |
| 26 Oct 2026 | Real-time aggregation on `sli_hourly` and `sli_daily` (migration `007`) | Off by default since 2.13: the newest hour was missing from Grafana until the next refresh |
| 26 Oct 2026 | `make backfill` refreshes the continuous aggregates as the owner after a long backfill | `bg_app` cannot refresh (owner only) and the policies only reach back 3 days, so older backfilled buckets never reached `sli_hourly` |
| 26 Oct 2026 | Snapshotter fills only *missing* buckets, with one range query per series, and writes nothing while Prometheus is unreachable | Rewriting a bucket older than retention would erase good history; 30-day backfill in a few calls instead of ~200k; an observer outage is not "no data" for the service |
| 26 Oct 2026 | Snapshotter zero-fills like the rules: no 5xx series = 0 bad; a latency SLI with no `good` series = every event bad; bad clamped to [0, total] | The 30-day bucket sums must agree with `bg:error_ratio:increase30d` (W4 exit), and `increase()` extrapolation can overshoot |
| 26 Oct 2026 | Snapshotter waits 45 s (three scrapes) before writing a bucket | The bucket's last samples must have landed, or the bucket is written short and never revisited |
| 26 Oct 2026 | `load_slos` refuses to snapshot when a spec's hash differs from its `slos` row | New queries must not be written under an id whose objective the database still records as the old one; `bg sync-slos` runs in the migrate job |
| 26 Oct 2026 | Service card: numbers are the first SLO's (availability); state and integrity are the worst across the service's SLOs; frozen = hard-stop alert or remaining < reserve; ETA only when the budget alone freezes | A burning latency SLO still turns the card AT_RISK; an alert-held freeze has no honest projection |
| 26 Oct 2026 | `daily_remaining` is the rolling 30-day remaining at the end of each of the last 30 days, summed from buckets | The sparkline plots budget remaining (0-100 axis); per-day sums from `sli_buckets` because the caggs miss backfills until refreshed |
| 26 Oct 2026 | `/metrics` probes Prometheus with `vector(1)` on every scrape for `bg_prometheus_reachable` | The breaker alone only notices an outage when a decision happens to query |
| 26 Oct 2026 | Log redaction installed as the log record factory | A handler filter misses records from third-party loggers (uvicorn access, httpx) that propagate past our handlers |
| 26 Oct 2026 | Integration tests use a TimescaleDB container plus `BG_TEST_DATABASE_URL` instead of testcontainers (plan W4.9) | Docker runs only inside WSL here; the CI `integration` job starts the image tag from `.env.versions` |
| 26 Oct 2026 | Compose secrets for the local stack are generated by `make secrets` into `deploy/compose/secrets/local.env`; GitHub App values default to placeholders | A local stack needs no hand-made secrets; the webhook rejects everything until the App exists (W5) |
| 26 Oct 2026 | Platform image built from the repo root with an allowlist `platform/Dockerfile.dockerignore`; SLO specs baked in | The specs are the source of truth and live outside `platform/` |
| 31 Oct 2026 | One GateInput per service: worst integrity across its SLOs, the most constrained SLO's budget, any SLO's page; under charge_origin a page counts when >= 50% of that SLO's last-hour bad events are self-inflicted, and an SLI without origin labels (latency) counts as self | The gate decides per deploy, not per SLO; "no attribution data" must never wave a page through as someone else's |
| 31 Oct 2026 | charge_origin inputs wired in W5 (plan puts them in W6.5) | Policy v1 says `attribution: charge_origin`; recording that while gating on all-errors numbers would make every record lie |
| 31 Oct 2026 | Change features are `ChangeFeatures.unknown()` (priced at size_max) until W6.1; history uses query 4.7 with the leak rule | Plan W5.1; the reason the price is high is visible in the stored facts |
| 31 Oct 2026 | The gate reads reserve and spend fraction from the policy file; a test fails if a spec's gate block disagrees | The policy is backtested (W7); the cards read the spec, and the two must not drift |
| 31 Oct 2026 | Shared types in `budgetguard/records.py` (`DeployFacts`, `to_gate_input`, JSON round trip) and `budgetguard/policy.py` | The backtester (W7) imports the gate's own code instead of copying it |
| 31 Oct 2026 | PostgreSQL down: the record goes to `bg:records-pending` (Valkey AOF) before the callback and is replayed; both stores down: no callback, the run waits | Rev 2 §8; keeps "recorded before GitHub is answered" in the degraded mode |
| 31 Oct 2026 | `DB_DOWN` = OSError, TimeoutError, OperationalError, InterfaceError, never DBAPIError | DBAPIError also covers SQL bugs; the first integration run showed a type bug buffered as if the database were down |
| 31 Oct 2026 | The callback comment is stored in `decisions.inputs.comment`; a redelivered job whose decision exists re-sends that comment if the callback never went out | A worker crash between commit and callback must not leave the run waiting forever, nor re-decide it |
| 31 Oct 2026 | A misconfigured App key (`AppMisconfigured`) fails the callback once and records `failed` | A permanent error retried by XAUTOCLAIM every minute would never end |
| 31 Oct 2026 | Deployment rows are written only for admitted decisions | A blocked deploy never ran; an `in_progress` row would inflate DORA deploy counts |
| 31 Oct 2026 | Verdict cache never stores an override decision | An override is single use; replaying its facts would admit twice |
| 31 Oct 2026 | Admin bypass recorded as a synthetic `ADMIN_BYPASS` decision (delivery `bypass:<deployment id>`) plus an overrides row | `overrides.decision_id` is NOT NULL; the deployment id keeps ingestion idempotent |
| 31 Oct 2026 | Cards freeze on the gate's number (self-only under charge_origin) and the ETA replays `self_bad` | The card and the gate must agree on "frozen" |
| 31 Oct 2026 | k3s: Traefik `internal` entrypoint (HelmChartConfig) + CoreDNS rewrite of `*.bg-apps.internal`; bg-apps NetworkPolicy admits only Traefik and monitoring | Canary weights must apply to service-to-service calls; the deployer Role cannot create NetworkPolicies, so a workflow edit cannot widen it |
| 31 Oct 2026 | Per-service chart values in `deploy/helm/service/values-<service>.yaml`; repository and digest are `required` | One chart, four releases; a release without a digest fails to render |
| 31 Oct 2026 | Bootstrap writes each secret value to a file in a private temp dir for `kubectl create secret --from-file` | No secret on a process command line; multi-line keys survive |
| 31 Oct 2026 | Actions pinned by SHA: checkout v7.0.1, setup-java v6.1.0, setup-node v7.1.0, setup-uv v10.3.0, setup-qemu v4.4.0, setup-buildx v4.4.1, login v4.6.0, build-push v7.4.0, cosign-installer v4.1.2, setup-helm v5.0.1, paths-filter v4.0.3; `scripts/lint_workflows.py` enforces it | Newest release older than two weeks on 31 Oct 2026 (security change #14) |
| 31 Oct 2026 | k3s v1.36.5+k3s1 (1.37.1 exists; stay on the line the kind node tests), Argo Rollouts v1.10.1, kube-prometheus-stack 92.10.1, cloudflared 2026.10.1, kubeconform v0.8.0, CRD catalog pinned to datreeio/CRDs-catalog@63669a5 | Newest older than two weeks; everything rendered passes kubeconform -strict on 1.36 |
| 31 Oct 2026 | `cosign verify --certificate-identity <build-push.yml@main> --certificate-github-workflow-sha $SHA` | A signed image of another commit cannot be swapped in (security change #13) |
| 31 Oct 2026 | Gate worker Valkey client: socket timeout 15 s | Above XREADGROUP's 5 s block; the default made idle reads crash the worker |
| 31 Oct 2026 | build-push publishes only on pushes to main with DOCKERHUB_USER / DOCKERHUB_NS / DOCKERHUB_TOKEN set; otherwise (and on PRs) it builds every image amd64-only without push or attestations | main went red after W5: the registry is not configured yet. Dockerfiles stay checked |
| 31 Oct 2026 | deploy.yml runs only after a published build and with DOCKERHUB_NS and BUDGETGUARD_URL set | A job naming a missing environment makes GitHub create it without the protection rule |
| 31 Oct 2026 | actionlint v1.7.12 in CI's workflow-lint step | Catches expression and context mistakes in workflows before they reach main |
| 31 Oct 2026 | Change features from the merged PR for the SHA, filtered to services/<service>/; a missing or malformed GitHub answer prices at size_max and the note goes into the comment and `inputs.change` | W6.1; a surprise from GitHub must not crash a decision |
| 31 Oct 2026 | Installation token for reads: contents: read + pull_requests: read | Trees (rollback proof) and the PR files (features, incident-fix lane) in one token per job |
| 31 Oct 2026 | Lane order rollback > override > incident fix; security lane = Dependabot/Renovate PR citing a GHSA id, recorded as INCIDENT_FIX with `kind: security` | One lane per decision; the gate chain still places incident_fix after integrity |
| 31 Oct 2026 | Incident-fix alertname validated as a metric name before it is placed in PromQL | It comes from an incident row the Alertmanager webhook wrote |
| 31 Oct 2026 | Change failure = a page incident in the 60-minute window AND self_bad/bad >= self_page_share; the window's self-inflicted cost is stored for every settled deploy | Query 4.9 as written blamed a deploy for a dependency's outage (B-15); `incidents.culprit_service_id` is never set |
| 31 Oct 2026 | A failed rollout (deployment_status failure/error) is a change failure immediately | The canary aborted or the job failed: the change failed |
| 31 Oct 2026 | Merged-PR events fill `pr_number` / `first_commit_at` only where the decision could not | The decision reads them from the PR API; the event is the fallback |
| 31 Oct 2026 | `freeze_impact` view: a block's freeze runs to the service's next decision (or now, max 7 days); admin-bypass rows excluded | Consecutive blocks partition time instead of double-counting |
| 31 Oct 2026 | Cards apply the gate's page rule: under charge_origin a page freezes the card only when the paging SLO's self share >= self_page_share (same `gather.self_share` query, unreachable Prometheus = 1.0) | The W6 exit showed orders "frozen" on its card while the gate admitted it during auth's outage |
| 31 Oct 2026 | W6 exit verified on fresh local history (compose volumes reset): during `make demo-dependency-outage` auth BLOCK `ACTIVE_PAGE` (self share 1.0), orders ADMIT `WITHIN_BUDGET` (self share 0.0, self-only budget 97.7% vs all-errors -5,053%); cards: auth frozen, orders not, "0% of spent budget is self-inflicted" | Plan W6 exit; the first run was confounded by the W5 exit's injected self errors still in orders' 30-day window |
| 31 Oct 2026 | backtest/ imports DeployFacts, to_gate_input and Policy from the gate (`budgetguard.records`, `budgetguard.policy`) instead of the reference's copies; the reference's AS-OF bootstrap queries are not carried over | The backtest must replay exactly the code that decides; no deploys predate BudgetGuard here |
| 31 Oct 2026 | W7 exit verified: a reserve of 3% fails `backtest.cli compare` (recall -6.3pp, harm recall -4.5pp; 11 fewer healthy blocks but 19 pp less burn prevented on 614 cases, 63 labelled failures); a test pins it | Plan W7 exit / Rev 2 demo step 7 |
| 31 Oct 2026 | Grounding lives in `budgetguard/grounding.py` (`evals/grounding.py` re-exports it) | The brief writer runs it online; `evals/` is not in the image |
| 31 Oct 2026 | Prompt dir and llm.yaml are settings (`BG_PROMPT_DIR`, `BG_LLM_CONFIG`); the image copies `platform/prompts` and `platform/config`; `EXTRAS="--extra llm"` builds a brief-writer image with the SDKs | The reference resolved them relative to the source tree, which breaks in an installed package; the default image ships no provider client |
| 31 Oct 2026 | The gate worker stores `inputs.eta` (`eta_utc`, `eta_in_hours`, half-budget time) | The brief explains the ETA from the decision record, never recomputes it |
| 31 Oct 2026 | Brief context: culprit = the service for an ACTIVE_PAGE or OVER_BUDGET block under charge_origin, none otherwise; still allowed = a rollback (+ a proven fix when an incident id is recorded), never override wording | Under charge_origin those blocks are self-inflicted by construction; the grounding check forbids override suggestions |
| 31 Oct 2026 | The brief is stored on the decision (`inputs.brief`, `brief_source`); the API serves it in place of the GitHub comment once written | One place for the dashboard to read; replay is unaffected (it reads only `gate_input`) |
| 31 Oct 2026 | Template brief eval: 120/120 grounded (Wilson lower bound 0.969), fact recall 1.0, no forbidden content | The fallback is held to 100% |
| 31 Oct 2026 | The default-model eval row needs `OPENAI_API_KEY` and `BG_EVAL_DSN`; without them brief-eval.yml skips the model job with a notice and still gates the template | Both are the user's accounts; the recorder itself is covered by an integration test |
| 31 Oct 2026 | policy-backtest.yml scores main's policy with the PR's code when main has no backtester yet | The PR that introduces the backtester has nothing to check out on main |
| 31 Oct 2026 | Every file read and write in backtest/ and evals/ is UTF-8 | The report's arrow crashed `compare` on Windows (cp1252); CI on Linux would never have shown it |
| 31 Oct 2026 | Manual `model` input of brief-eval.yml goes through `env:` and is validated as a model name | Never `${{ }}` inside `run:` (template injection) |
