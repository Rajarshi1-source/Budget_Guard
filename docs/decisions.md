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
| 26 Oct 2026 | `astral-sh/setup-uv@v10` (reference had v6, last released Sep 2025) | Current major; SHA pins follow in W5 |
