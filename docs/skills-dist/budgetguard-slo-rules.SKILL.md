---
name: budgetguard-slo-rules
description: Author BudgetGuard's SLO-as-code pipeline — the ServiceLevel YAML spec per service, the Python rule generator (bg generate and bg generate --check), Prometheus 3 recording rules for every burn window, multi-window multi-burn-rate page and ticket alerts with a traffic floor, the SLIIntegrityLost alert, latency SLIs from histogram buckets, promtool rule unit tests, the Sloth parity check, Alertmanager routing and inhibition, blackbox probes, 35-day retention and the PrometheusRule CRs. Use this skill whenever writing or debugging PromQL, recording rules, alert rules, SLO YAML, promtool tests, Alertmanager config or Sloth/Pyrra/OpenSLO interop for BudgetGuard — including an alert that never fires, a burn-rate rule that returns empty, a missing window, or a question like why two windows per alert — even if the user only pastes a PromQL expression. MANDATE - rules are generated from one spec and never hand-edited; every window an alert references has a recording rule and a promtool test; silence is not health.
---

# BudgetGuard — SLO-as-code and Prometheus rules (Rev 2)

One spec file per service is the source of truth. A generator turns it into recording rules,
alerts, gate inputs and dashboard variables, so the Rev 1 bug class B-4 (an alert reading a
`…:30m` rule nobody generated, which evaluates to empty and never fires) **cannot exist**:
the generator emits every window it references and `promtool` proves it.

Pins (Rev 2 §C, verified 10 Oct 2026 — re-check at kickoff): Prometheus **3.13.x LTS**,
Alertmanager **0.34.1**, kube-prometheus-stack chart matching Prometheus 3.13, Sloth
**0.16.0** and Pyrra **0.10.1** as parity references, OpenSLO v1 as an import format.

Repo paths: `slo/*.yaml` (specs), `platform/budgetguard/rulegen/` (generator, CLI `bg`),
`rules/generated/` (output, committed, never hand-edited), `rules/tests/` (promtool),
`deploy/compose/{prometheus,alertmanager}.yml`.

Pairs with: `budgetguard-spring-services` (metric names and labels),
`budgetguard-slo-math` (the numbers these rules must agree with),
`budgetguard-delivery-platform` (ships rules as `PrometheusRule` CRs).

---

## 1. The spec — `slo/orders-service.yaml`

```yaml
apiVersion: budgetguard.dev/v1
kind: ServiceLevel
metadata:
  service: orders-service
  owner: team-commerce
spec:
  window: 30d
  failure_mode: fail_open          # gate behaviour when SLI integrity is NO_DATA/STALE
  min_events_per_hour: 600         # traffic floor for short windows
  job: orders-service              # Prometheus scrape job, used by the integrity alert
  probe: http://orders-service.bg-apps:8080/actuator/health/readiness
  slos:
    - name: availability
      objective: 0.999
      sli:
        type: ratio
        total: http_server_requests_seconds_count{service="orders-service",uri!~"/actuator.*"}
        bad: http_server_requests_seconds_count{service="orders-service",uri!~"/actuator.*",status=~"5.."}
        self_bad: http_server_requests_seconds_count{service="orders-service",uri!~"/actuator.*",status=~"5..",error_origin="self"}
      alerting:
        page:
          - {long: 1h, short: 5m, burn: 14.4}     # 2% of budget in 1h
          - {long: 6h, short: 30m, burn: 6}       # 5% of budget in 6h
        ticket:
          - {long: 3d, short: 6h, burn: 1}        # 10% of budget in 3d
    - name: latency
      objective: 0.99
      sli:
        type: latency_bucket
        total: http_server_requests_seconds_count{service="orders-service",uri!~"/actuator.*"}
        good: http_server_requests_seconds_bucket{service="orders-service",uri!~"/actuator.*",le="0.5"}
      alerting:
        page:   [{long: 1h, short: 5m, burn: 14.4}]
        ticket: [{long: 3d, short: 6h, burn: 1}]
  gate:
    reserve_pct: 5
    spend_fraction: 0.5
    hard_stop_on: [page]
```

Design notes worth saying out loud:

- The **6x/6h tier pages** here, matching the Workbook. Rev 1 ticketed it (B-12); if you
  choose to ticket it, the spec is where that policy lives — not a hand-edited rule.
- `uri!~"/actuator.*"` keeps probes and scrapes out of the user-facing SLI.
- `le="0.5"`: Prometheus 3 normalises classic-histogram `le` values to float form
  (`"1"` becomes `"1.0"`), so always write the float form. Buckets only exist because
  `budgetguard-spring-services` configures `management.metrics.distribution.slo`.
- Async notifications use `type: latency_bucket` over the custom
  `notification_delivery_seconds` timer (end-to-end from enqueue), not the HTTP 202.

### Spec model — `rulegen/spec.py`

```python
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DURATION = re.compile(r"^\d+[smhd]$")


class Tier(BaseModel):
    long: str
    short: str
    burn: float = Field(gt=0)

    @field_validator("long", "short")
    @classmethod
    def _dur(cls, v: str) -> str:
        if not DURATION.match(v):
            raise ValueError(f"bad duration {v}")
        return v


class Alerting(BaseModel):
    page: list[Tier] = []
    ticket: list[Tier] = []


class Sli(BaseModel):
    type: Literal["ratio", "latency_bucket"]
    total: str
    bad: str | None = None
    good: str | None = None
    self_bad: str | None = None


class Slo(BaseModel):
    name: str
    objective: float = Field(gt=0, lt=1)
    sli: Sli
    alerting: Alerting


class Spec(BaseModel):
    window: Literal["30d", "28d"] = "30d"
    failure_mode: Literal["fail_open", "fail_closed"] = "fail_open"
    min_events_per_hour: int = Field(ge=0)
    job: str
    probe: str | None = None
    slos: list[Slo]


class Metadata(BaseModel):
    service: str
    owner: str


class ServiceLevel(BaseModel):
    apiVersion: Literal["budgetguard.dev/v1"]
    kind: Literal["ServiceLevel"]
    metadata: Metadata
    spec: Spec
```

---

## 2. The generator — `rulegen/generate.py`

```python
"""ServiceLevel spec -> Prometheus rule groups. Deterministic output (sorted) so --check can diff."""
from __future__ import annotations

import yaml

from .spec import ServiceLevel, Slo


class _RuleDumper(yaml.SafeDumper):
    """Multi-line PromQL as `|` blocks, and no &id001 anchors for shared label dicts."""
    def ignore_aliases(self, data):
        return True


def _str(dumper: yaml.SafeDumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_RuleDumper.add_representer(str, _str)


def dump_rules(groups: list[dict]) -> str:
    return yaml.dump({"groups": groups}, Dumper=_RuleDumper, sort_keys=False, width=200)


def _windows(slo: Slo) -> list[str]:
    ws = {"5m", "30m", "1h", "6h", "3d"}                     # gate + dashboards always need these
    for tier in slo.alerting.page + slo.alerting.ticket:
        ws |= {tier.long, tier.short}
    order = {"m": 60, "h": 3600, "d": 86400}
    return sorted(ws, key=lambda w: int(w[:-1]) * order[w[-1]])


def _ratio(num: str, den: str, w: str) -> str:
    return (f"sum by (service) (rate({num}[{w}]))\n/\n"
            f"sum by (service) (rate({den}[{w}]))")


def _bad_ratio(bad: str, total: str, w: str, fn: str = "rate") -> str:
    """0, not empty, when there is traffic but no error series (found in W2, see below)."""
    num = f"sum by (service) ({fn}({bad}[{w}]))"
    den = f"sum by (service) ({fn}({total}[{w}]))"
    return f"(\n{num}\nor\n0 * {den}\n)\n/\n{den}"


def _error_ratio_expr(slo: Slo, w: str) -> str:
    if slo.sli.type == "ratio":
        return _bad_ratio(slo.sli.bad, slo.sli.total, w)
    return f"1 - (\n{_bad_ratio(slo.sli.good, slo.sli.total, w)}\n)"     # zero-fill the good side too


def recording_groups(sl: ServiceLevel) -> list[dict]:
    svc = sl.metadata.service
    groups = []
    for slo in sl.spec.slos:
        labels = {"slo": slo.name}
        rules = [{"record": "bg:slo_allowed_ratio", "labels": {**labels, "service": svc},
                  "expr": f"vector({1 - slo.objective:.10g})"}]
        for w in _windows(slo):
            rules.append({"record": f"bg:error_ratio:rate{w}", "labels": labels,
                          "expr": _error_ratio_expr(slo, w)})
            if slo.sli.self_bad:
                rules.append({"record": f"bg:self_error_ratio:rate{w}", "labels": labels,
                              "expr": _bad_ratio(slo.sli.self_bad, slo.sli.total, w)})
        rules.append({"record": "bg:requests:rate1h", "labels": labels,
                      "expr": f"sum by (service) (rate({slo.sli.total}[1h]))"})
        groups.append({"name": f"bg:{svc}:{slo.name}:ratios", "interval": "30s", "rules": rules})
        thirty = (_ratio(slo.sli.bad, slo.sli.total, sl.spec.window).replace("rate(", "increase(")
                  if slo.sli.type == "ratio"
                  else "1 - (\n" + _ratio(slo.sli.good, slo.sli.total, sl.spec.window).replace("rate(", "increase(") + "\n)")
        groups.append({"name": f"bg:{svc}:{slo.name}:window", "interval": "5m",
                       "rules": [{"record": f"bg:error_ratio:increase{sl.spec.window}",
                                  "labels": labels, "expr": thirty}]})
    return groups


def alert_groups(sl: ServiceLevel) -> list[dict]:
    svc = sl.metadata.service
    rules: list[dict] = []
    for slo in sl.spec.slos:
        a = 1 - slo.objective
        sel = f'{{service="{svc}",slo="{slo.name}"}}'
        floor = (f'and on (service, slo) (bg:requests:rate1h{sel} * 3600 >= '
                 f'{sl.spec.min_events_per_hour})')
        for severity, tiers in (("page", slo.alerting.page), ("ticket", slo.alerting.ticket)):
            if not tiers:
                continue
            clauses = [f"(bg:error_ratio:rate{t.long}{sel} > ({t.burn} * {a:.10g})\n"
                       f" and bg:error_ratio:rate{t.short}{sel} > ({t.burn} * {a:.10g}))"
                       for t in tiers]
            expr = "(\n" + "\n or\n".join(clauses) + "\n)\n" + floor
            rules.append({
                "alert": f"ErrorBudgetBurn{severity.capitalize()}",
                "expr": expr,
                "labels": {"severity": severity, "service": svc, "slo": slo.name,
                           "incident_type": f"{slo.name}_{'burn' if severity == 'page' else 'slow_burn'}"},
                "annotations": {"summary": f"{svc} {slo.name} is spending error budget"
                                           f" {'fast' if severity == 'page' else 'steadily'}",
                                "budgetguard_url": f"https://budgetguard.example.dev/services/{svc}"},
            })
    integrity = [f'up{{job="{sl.spec.job}"}} == 0',
                 f'absent(http_server_requests_seconds_count{{service="{svc}"}})']
    if sl.spec.probe:
        integrity.append(f'probe_success{{job="blackbox",service="{svc}"}} == 0')   # label, not URL (W2)
    rules.append({"alert": "SLIIntegrityLost", "expr": "\nor ".join(integrity), "for": "2m",
                  "labels": {"severity": "page", "service": svc, "incident_type": "sli_integrity"},
                  "annotations": {"summary": f"{svc}: SLI signal lost; gate reports UNKNOWN"}})
    return [{"name": f"bg:{svc}:alerts", "rules": rules}]
```

CLI (`rulegen/cli.py`, exposed as `bg` via `pyproject.toml` scripts):

- `bg generate slo/ -o rules/generated/` — writes `<service>.rules.yaml` via `dump_rules()`
  (recording groups, then alert groups). Plain `yaml.safe_dump` emits `&id001` anchors for the
  shared label dicts and quoted one-line PromQL — valid, but unreadable in review.
- `bg generate --check` — regenerates into a temp dir and fails CI if it differs from the
  committed files (the "nobody hand-edited a rule" guarantee).
- `bg generate --format prometheus-operator` — wraps groups in a `PrometheusRule` CR with
  `metadata.labels.release: kube-prometheus-stack` so the operator selects it.

**Zero errors must read 0, not empty (bug in the first version of this generator, found in W2 by
running the rules against live traffic).** A healthy service has no 5xx series at all, so
`sum(rate(bad)) / sum(rate(total))` is an *empty vector*: "zero errors" looked exactly like "no data",
which the gate maps to UNKNOWN, and every self-error ratio (D-3) was empty. `sum(rate(bad)) or
0 * sum(rate(total))` fills the gap only while traffic exists; with no traffic the ratio stays empty,
so D-4 still holds. The good side of latency SLIs gets the same fill: if every notification fails,
the `outcome="delivered"` bucket never exists and the SLI must read 1.0, not nothing.

**Probe matched by label, not URL (W2).** The same committed rules run in compose and k3s, where the
probe URLs differ, so `SLIIntegrityLost` matches `probe_success{job="blackbox",service="<svc>"}`.
Prometheus derives that label from the target host (relabel in §5). In k3s the PodMonitor needs
`jobLabel: app` so `up{job="<service>"}` also matches (kube-prometheus-stack otherwise uses
`<namespace>/<podmonitor>` as the job).

Gaps from Rev 2 that the generator closes:

- `bg:slo_allowed_ratio` is used by the Rev 2 Grafana burn-rate panel (§11.1) but never
  defined; it is emitted here as a constant series per SLO.
- Page alerts carry no `for:` — the short window already provides the confirmation delay
  (B-12). Only `SLIIntegrityLost` keeps `for: 2m` to ride out a single failed scrape.

---

## 3. promtool tests — every window, every tier

`rules/tests/orders-page.test.yaml` (generated rule files loaded as-is):

```yaml
rule_files:
  - ../generated/orders-service.rules.yaml
evaluation_interval: 30s
tests:
  - name: fast burn pages (1% errors on a 99.9% SLO = 10x; use 2% = 20x)
    interval: 1m
    input_series:
      - series: 'http_server_requests_seconds_count{service="orders-service",uri="/orders",status="200",error_origin="none"}'
        values: '0+980x180'
      - series: 'http_server_requests_seconds_count{service="orders-service",uri="/orders",status="500",error_origin="self"}'
        values: '0+20x180'
      - series: 'up{job="orders-service"}'
        values: '1x180'
    alert_rule_test:
      - eval_time: 70m
        alertname: ErrorBudgetBurnPage
        exp_alerts:
          - exp_labels: {severity: page, service: orders-service, slo: availability, incident_type: availability_burn}
            exp_annotations:
              summary: "orders-service availability is spending error budget fast"
              budgetguard_url: "https://budgetguard.example.dev/services/orders-service"
    promql_expr_test:
      - expr: round(bg:error_ratio:rate1h{service="orders-service",slo="availability"}, 0.0001)
        eval_time: 70m
        exp_samples:                         # round(): promtool compares floats exactly
          - labels: '{service="orders-service",slo="availability"}'
            value: 0.02

  - name: below traffic floor stays quiet
    interval: 1m
    input_series:
      - series: 'http_server_requests_seconds_count{service="orders-service",uri="/orders",status="200",error_origin="none"}'
        values: '0+7x180'                    # 7 + 2 = 9/min = 540/h < 600 floor
      - series: 'http_server_requests_seconds_count{service="orders-service",uri="/orders",status="500",error_origin="self"}'
        values: '0+2x180'
      - series: 'up{job="orders-service"}'
        values: '1x180'
    alert_rule_test:
      - eval_time: 70m
        alertname: ErrorBudgetBurnPage
        exp_alerts: []

  - name: dead target raises integrity alert, not silence
    interval: 1m
    input_series:
      - series: 'up{job="orders-service"}'
        values: '0x10'
    alert_rule_test:
      - eval_time: 5m
        alertname: SLIIntegrityLost
        exp_alerts:                          # two series: the up==0 branch and the absent() branch
          - exp_labels: {severity: page, service: orders-service, incident_type: sli_integrity, job: orders-service}
            exp_annotations:
              summary: "orders-service: SLI signal lost; gate reports UNKNOWN"
          - exp_labels: {severity: page, service: orders-service, incident_type: sli_integrity}
            exp_annotations:
              summary: "orders-service: SLI signal lost; gate reports UNKNOWN"
```

Generate one test file per service per tier from a template in CI, plus these hand-written
edge cases. Three lessons these tests encode (each was a real failure when this file was
first run against promtool 3.13.2):

- **Floats compare exactly** — 0.02 came back as 0.020000000000000004. Wrap value checks in
  `round(..., 0.0001)`, which also drops `__name__`, so the expected labels lose the metric name.
- **The floor counts all requests**, errors included: 8 good + 2 bad per minute is 600/h,
  which *meets* a 600 floor. Size fixtures from the total.
- **An `or` of integrity signals fires once per branch**: a dead target with no samples raises
  both the `up == 0` series (carries `job`) and the `absent()` series (does not). Expect both;
  Alertmanager groups them by `service`.

- **Fixtures must look like real services (W2):** with the zero-fill, a request counter without its
  histogram buckets makes a latency SLI on the same counter read 100% bad, which is the right
  production behaviour (lost bucket config pages loudly). Availability fixtures therefore also carry
  an all-fast `le` bucket.
- **Stay off .5 rounding boundaries (W2):** PromQL `round()` is half-up and Python's is not
  (0.00075 → 0.0008 vs 0.0007). Generated tests use 1.6x (fires) and 0.4x (quiet) the burn.
- **Glob inside the container:** `docker run … promtool test rules generated/*.yaml` expands the
  glob on the host; run `sh -c 'promtool …'` with `--entrypoint sh` instead.

`bg tests ../slo -o ../rules/tests/generated [--check]` writes one file per service and severity
(every SLO and tier, plus a quiet case) and CI checks them like the rules. Hand-written edge cases
(`rules/tests/*.test.yaml`) cover the floor, dead target, failing probe, the 2m blip, zero errors =
0, and no traffic = empty.

When a test fails with "unexpected labels", read the diff, don't loosen the rule.

---

## 4. Sloth parity check (D-8 interop)

Goal: prove BudgetGuard's ratios equal Sloth's for the same SLO, so a team on Sloth can adopt
the gate without trusting new math.

1. `bg export sloth slo/orders-service.yaml > /tmp/orders.sloth.yaml` (map `objective` × 100,
   `bad`→`errorQuery`, `total`→`totalQuery`, with `[{{.window}}]` placeholders).
2. `sloth generate -i /tmp/orders.sloth.yaml -o rules/parity/orders.sloth.rules.yaml`
3. A promtool test loads both rule files on identical input series and asserts
   `slo:sli_error:ratio_rate1h{sloth_service="orders-service",sloth_slo="availability"}`
   and `bg:error_ratio:rate1h{service="orders-service",slo="availability"}` produce the same
   value at several eval times (5m, 30m, 1h, 6h windows).

Run it in `ci.yml`; a mismatch means one of the generators changed semantics.

---

## 5. Prometheus and Alertmanager config

```yaml
# deploy/compose/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 30s
rule_files: [/etc/prometheus/rules/*.yaml]
alerting:
  alertmanagers: [{static_configs: [{targets: ["alertmanager:9093"]}]}]
scrape_configs:
  - job_name: auth-service
    metrics_path: /actuator/prometheus
    static_configs: [{targets: ["auth-service:8080"]}]
  # … orders-service, inventory-service, notifications-service identical
  - job_name: blackbox
    metrics_path: /probe
    params: {module: [http_2xx]}
    static_configs:
      - targets:
          - http://auth-service:8080/actuator/health/readiness
          - http://orders-service:8080/actuator/health/readiness
          - http://inventory-service:8080/actuator/health/readiness
          - http://notifications-service:8080/actuator/health/readiness
    relabel_configs:
      - {source_labels: [__address__], target_label: __param_target}
      - {source_labels: [__param_target], target_label: instance}
      - {target_label: __address__, replacement: blackbox:9115}
      - {source_labels: [__param_target], regex: 'https?://([a-z0-9-]+)[.:/].*', target_label: service}
```

Start Prometheus with `--storage.tsdb.retention.time=35d` (B-10: the 15-day default silently
halves a 30-day window). The last relabel gives each probe the `service` label the integrity
alert matches on.

```yaml
# deploy/compose/alertmanager.yml
route:
  receiver: slack-tickets
  group_by: [service, slo, severity]
  routes:
    - matchers: [severity="page"]
      receiver: page
      continue: true
    - matchers: [severity="page"]
      receiver: budgetguard
    - matchers: [severity="ticket"]
      receiver: budgetguard
      continue: true
receivers:
  - name: page
    slack_configs: [{api_url_file: /etc/alertmanager/slack_page_url, channel: "#bg-page", send_resolved: true}]
  - name: slack-tickets
    slack_configs: [{api_url_file: /etc/alertmanager/slack_ticket_url, channel: "#bg-tickets", send_resolved: true}]
  - name: budgetguard
    webhook_configs:
      - url: http://platform-api:8000/v1/webhooks/alertmanager
        send_resolved: true
        http_config: {authorization: {credentials_file: /etc/alertmanager/bg_token}}
inhibit_rules:
  - source_matchers: [severity="page"]
    target_matchers: [severity="ticket"]
    equal: [service, slo]
```

Why 0.34.1 and not the portfolio's 0.33.1: 0.34.1 fixes alerts being wrongly un-muted by
inhibition — exactly the page-inhibits-ticket rule above.

The gate reads active pages from Prometheus's own `ALERTS{alertstate="firing",severity="page"}`,
so an Alertmanager outage never changes a verdict.

---

## 6. Debugging recipes

| Symptom | Check |
| --- | --- |
| Alert never fires | `promtool test rules`; then in the UI query each referenced `bg:error_ratio:rate<w>` — empty means a missing window or a label mismatch |
| Ratio is NaN | zero traffic in the window; expected — the gate maps it to UNKNOWN, not healthy |
| 30-day number looks too good | retention < 35d, or a dashboard averaging ratios instead of summing counts |
| Latency SLI empty | no `_bucket` series: SLO boundaries missing in `application.yml`, or `le` written as `"1"` |
| Page fires at 3 a.m. on one error | traffic floor missing from the expression |

## 7. Interview soundbites

- "Two windows per alert: the long one proves it matters, the short one proves it's still
  happening — so the page also clears minutes after recovery."
- "My rules are generated, and CI fails if anyone hand-edits one. That's how I know every
  window an alert references actually exists."
- "I diff my generator against Sloth in promtool. Same input series, same ratios — teams on
  Sloth can adopt the gate without trusting new math."
