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

Not yet checked: kube-prometheus-stack chart, cosign, syft, LLM prices, GitHub custom
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
