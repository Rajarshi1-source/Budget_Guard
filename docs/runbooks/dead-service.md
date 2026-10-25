# Runbook: a service is dead or unscraped (SLIIntegrityLost)

**Why it matters (D-4):** a crashed or unscraped service emits no samples. A naive burn-rate rule
then reads an empty ratio, every `> 14.4` comparison is false, and the service looks healthy.
BudgetGuard classifies the signal first: missing data is UNKNOWN, never zero errors.

## What you will see

| Signal | Value | Source |
| --- | --- | --- |
| Alert | `SLIIntegrityLost{service="<svc>"}`, severity page, `for: 2m` | `rules/generated/<svc>.rules.yaml` |
| Branches | one alert per branch that matches: `up{job="<svc>"} == 0`, `absent(http_server_requests_seconds_count{service="<svc>"})`, `probe_success{job="blackbox",service="<svc>"} == 0` | same |
| Routing | `page` (Slack #bg-page) and `budgetguard` (platform incident table) | `deploy/compose/alertmanager.yml` |
| Verdict | `bg status` shows the service's SLOs as `unknown` with integrity `no_data` or `stale` | `platform/budgetguard/signals.py` |
| Gate (W5) | the service's `failure_mode` applies: fail-open admits as `admit_unverified` and pages; a rollback is still admitted first | `budgetguard-admission-gate` |

## Measured in the W3.6 chaos check (compose, 26 Oct 2026)

`docker compose stop orders-service` with 20 req/s of k6 traffic running:

| Time after stop | State |
| --- | --- |
| +46 s | `SLIIntegrityLost` pending (series marked stale; `up == 0`; probe failing) |
| +167 s | firing (pending + `for: 2m`); Alertmanager holds 3 active alerts routed to `page` and `budgetguard` |
| — | `bg status`: orders availability and latency `unknown` / `no_data`; the other services stay `healthy` |
| +31 s after restart | alert resolved; orders back to `healthy` |

## Steps

1. Confirm what the platform sees: `make status` (or `cd platform && uv run bg status ../slo`).
2. Check the target and the probe in Prometheus: `up{job="<svc>"}`,
   `probe_success{job="blackbox",service="<svc>"}`, and Status → Targets for the scrape error.
3. Check the workload: `docker compose ps` / `kubectl -n bg-apps get pods -l app=<svc>`; logs.
4. If the service is up but unscraped, fix scraping (PodMonitor `jobLabel: app` in k3s, network,
   `/actuator/prometheus` exposure) before trusting any verdict.
5. While it is UNKNOWN, deploys follow the service's `failure_mode`; a rollback is always admitted.

## Reproduce

```bash
make up && make load &        # inside WSL Ubuntu-D
make chaos-dead-service       # stops orders-service
make status                   # orders: unknown / no_data
docker compose --env-file deploy/compose/.env.versions -f deploy/compose/docker-compose.yml start orders-service
```
