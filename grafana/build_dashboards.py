"""Writes grafana/dashboards/service-reliability.json (W4.8). Run: python grafana/build_dashboards.py

Panels: burn rate per window (Prometheus), 30-day availability from sli_hourly (request-weighted:
sum(bad)/sum(total), never an average of ratios), MTTR p50/p90 per incident type (query 4.3),
deploy frequency and change-failure rate (query 4.4), minutes of degraded SLI integrity,
chargeback (query 4.6, D-3) and freeze impact (freeze_impact view, W6.6).
"""
from __future__ import annotations

import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "prometheus"}
TSDB = {"type": "grafana-postgresql-datasource", "uid": "timescaledb"}


def sql(ref: str, raw: str, fmt: str = "table") -> dict:
    return {"refId": ref, "datasource": TSDB, "editorMode": "code", "format": fmt, "rawQuery": True,
            "rawSql": raw.strip()}


def panel(pid: int, title: str, kind: str, grid: tuple[int, int, int, int], targets: list[dict],
          description: str, **extra: object) -> dict:
    x, y, w, h = grid
    return {"id": pid, "title": title, "type": kind, "description": description,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets, **extra}


burn = panel(
    1, "Burn rate (availability)", "timeseries", (0, 0, 24, 9),
    [{"refId": w, "datasource": PROM, "legendFormat": f"{{{{service}}}} {w}",
      "expr": f'bg:error_ratio:rate{w}{{service=~"$service",slo="availability"}}'
              f" / on (service, slo) bg:slo_allowed_ratio"} for w in ("5m", "1h", "6h")],
    "Error ratio divided by the allowed ratio. 1 = spending exactly on plan; 14.4 spends 2% of the "
    "30-day budget per hour (the page threshold), 6 spends 5% in 6 h.",
    fieldConfig={"defaults": {"unit": "x", "min": 0,
                              "thresholds": {"mode": "absolute", "steps": [
                                  {"color": "green", "value": None}, {"color": "orange", "value": 6},
                                  {"color": "red", "value": 14.4}]},
                              "custom": {"thresholdsStyle": {"mode": "line"}}}, "overrides": []})

availability = panel(
    2, "30-day good-event ratio per SLO", "table", (0, 9, 12, 9),
    [sql("A", """
SELECT s.name AS service, o.name AS slo, 100 * o.objective AS objective_pct,
       100 * (1 - sum(h.bad) / NULLIF(sum(h.total), 0)) AS good_pct,
       100 * (1 - (sum(h.bad) / NULLIF(sum(h.total), 0)) / (1 - o.objective)) AS budget_remaining_pct,
       sum(h.total) AS events
FROM sli_hourly h JOIN slos o ON o.id = h.slo_id JOIN services s ON s.id = o.service_id
WHERE o.active AND h.hour >= now() - INTERVAL '30 days' AND s.name IN ($service)
GROUP BY s.name, o.name, o.objective ORDER BY 1, 2
""")],
    "From the snapshotter's 5-minute buckets via the sli_hourly continuous aggregate (real-time, so the "
    "current hour is included). For availability SLOs good_pct is the availability; for latency SLOs "
    "it is the share of requests under the threshold. Remaining budget may be negative.",
    fieldConfig={"defaults": {"decimals": 3}, "overrides": [
        {"matcher": {"id": "byName", "options": "events"}, "properties": [{"id": "decimals", "value": 0}]}]})

mttr = panel(
    3, "MTTR per incident type (30 days)", "table", (12, 9, 12, 9),
    [sql("A", """
SELECT incident_type, count(*) AS incidents,
       extract(epoch FROM percentile_cont(0.5) WITHIN GROUP (ORDER BY resolved_at - started_at)) / 60 AS p50_min,
       extract(epoch FROM percentile_cont(0.9) WITHIN GROUP (ORDER BY resolved_at - started_at)) / 60 AS p90_min
FROM incidents
WHERE resolved_at IS NOT NULL AND started_at >= now() - INTERVAL '30 days'
GROUP BY incident_type ORDER BY p50_min DESC
""")],
    "Median and p90, never the mean: one nine-hour incident must not hide ten five-minute ones. "
    "Incidents come from Alertmanager's webhook to the platform.",
    fieldConfig={"defaults": {"decimals": 1}, "overrides": []})

dora = panel(
    4, "Deploy frequency and change-failure rate (30 days)", "table", (0, 18, 12, 8),
    [sql("A", """
SELECT s.name AS service,
       count(d.id) / 30.0 AS deploys_per_day,
       round(100.0 * count(*) FILTER (WHERE d.is_change_failure)
             / NULLIF(count(*) FILTER (WHERE d.is_change_failure IS NOT NULL), 0), 1) AS cfr_pct,
       extract(epoch FROM percentile_cont(0.5) WITHIN GROUP (ORDER BY d.finished_at - d.first_commit_at))
         / 3600 AS lead_time_p50_h
FROM services s
LEFT JOIN deployments d ON d.service_id = s.id
     AND d.started_at >= now() - INTERVAL '30 days' AND d.status <> 'in_progress'
WHERE s.name IN ($service)
GROUP BY s.name ORDER BY 1
""")],
    "DORA keys from the deployments table (filled by deployment_status events from W6). Only "
    "self-inflicted burn marks a change failure (D-3).",
    fieldConfig={"defaults": {"decimals": 2}, "overrides": []})

integrity = panel(
    5, "Minutes of degraded SLI integrity", "timeseries", (12, 18, 12, 8),
    [sql("A", """
SELECT h.hour AS time, s.name || ' ' || o.name AS metric, 5 * h.degraded_buckets AS value
FROM sli_hourly h JOIN slos o ON o.id = h.slo_id JOIN services s ON s.id = o.service_id
WHERE $__timeFilter(h.hour) AND o.active AND s.name IN ($service)
ORDER BY 1
""", fmt="time_series")],
    "Per hour, minutes whose bucket was low_traffic, stale or no_data. A dead service shows up here "
    "instead of as a perfect error rate (D-4).",
    fieldConfig={"defaults": {"unit": "m", "min": 0, "custom": {"drawStyle": "bars", "fillOpacity": 60}},
                 "overrides": []})

chargeback = panel(
    6, "Chargeback: who caused the bad events (30 days)", "table", (0, 26, 12, 8),
    [sql("A", """
SELECT s.name AS service,
       round((100.0 * sum(b.self_bad) / NULLIF(sum(b.bad), 0))::numeric, 1) AS self_share_pct,
       round((sum(b.bad) - sum(b.self_bad))::numeric, 0) AS dependency_bad_events,
       round(sum(b.bad)::numeric, 0) AS bad_events
FROM sli_buckets b JOIN slos o ON o.id = b.slo_id JOIN services s ON s.id = o.service_id
WHERE o.name = 'availability' AND b.bucket_start >= now() - INTERVAL '30 days' AND s.name IN ($service)
GROUP BY s.name ORDER BY 1
""")],
    "Query 4.6 (D-3). Self share is what the gate charges under attribution charge_origin; the "
    "rest was caused by a dependency, so it freezes the dependency, not this service.",
    fieldConfig={"defaults": {"decimals": 1}, "overrides": []})

freeze = panel(
    7, "Freeze impact: blocked deploys and freeze minutes", "timeseries", (12, 26, 12, 8),
    [sql("A", """
SELECT day AS time, service || ' freeze min' AS metric, freeze_minutes AS value
FROM freeze_impact WHERE $__timeFilter(day) AND service IN ($service)
UNION ALL
SELECT day, service || ' blocked', blocked_deploys
FROM freeze_impact WHERE $__timeFilter(day) AND service IN ($service)
ORDER BY 1
""", fmt="time_series")],
    "From the freeze_impact view over decisions. A block freezes the service until its next decision; "
    "the backtester (W7) compares these minutes across policies as false-freeze minutes.",
    fieldConfig={"defaults": {"min": 0, "custom": {"drawStyle": "bars", "fillOpacity": 50}}, "overrides": []})

dashboard = {
    "uid": "bg-service-reliability",
    "title": "BudgetGuard / Service reliability",
    "tags": ["budgetguard", "slo"],
    "timezone": "browser",
    "schemaVersion": 41,
    "version": 1,
    "editable": False,
    "refresh": "1m",
    "time": {"from": "now-24h", "to": "now"},
    "templating": {"list": [{
        "name": "service", "label": "Service", "type": "query", "datasource": PROM,
        "query": {"query": "label_values(bg:slo_allowed_ratio, service)", "refId": "service"},
        "definition": "label_values(bg:slo_allowed_ratio, service)",
        "includeAll": True, "multi": True, "current": {"text": "All", "value": "$__all"}, "refresh": 2}]},
    "panels": [burn, availability, mttr, dora, integrity, chargeback, freeze],
}

out = Path(__file__).resolve().parent / "dashboards" / "service-reliability.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8", newline="\n")
print(f"wrote {out}")
