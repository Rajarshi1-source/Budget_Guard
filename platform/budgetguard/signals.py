"""Gather live SLI signals from Prometheus and turn them into tri-state verdicts (D-4).

Inputs for integrity.classify() and integrity.sli_state(), per service and SLO. In W3 the 30-day
remaining budget comes from the bg:error_ratio:increase30d recording rule; from W4 the gate reads
TimescaleDB buckets and keeps this rule as the fallback (Rev 2 §11.2).
"""
from __future__ import annotations

from dataclasses import dataclass

from .integrations.prometheus import PrometheusClient, PrometheusUnavailable
from .integrity import Integrity, SignalHealth, SliState, classify, sli_state
from .rulegen.spec import ServiceLevel, Slo


@dataclass(frozen=True, slots=True)
class SloStatus:
    service: str
    slo: str
    integrity: Integrity
    state: SliState
    remaining_pct: float | None        # 30-day; negative = overspent
    burn_rate_1h: float | None
    page_firing: bool
    ticket_firing: bool
    events_last_hour: float


def _sel(svc: str, slo: str) -> str:
    return f'{{service="{svc}",slo="{slo}"}}'


async def signal_health(prom: PrometheusClient, sl: ServiceLevel, slo: Slo) -> SignalHealth:
    svc = sl.metadata.service
    total = slo.sli.total
    present = await prom.scalar(f"count({total})")
    up = await prom.scalar(f'min(up{{job="{sl.spec.job}"}})')
    probe = None
    if sl.spec.probe:
        probe = await prom.scalar(f'min(probe_success{{job="blackbox",service="{svc}"}})')
    events = await prom.scalar(f"sum(increase({total}[1h]))")
    return SignalHealth(
        series_present=bool(present),
        targets_up=None if up is None else up >= 1,
        probe_ok=None if probe is None else probe >= 1,
        events_last_hour=events or 0.0,
        min_events_per_hour=float(sl.spec.min_events_per_hour),
    )


async def slo_status(prom: PrometheusClient, sl: ServiceLevel, slo: Slo) -> SloStatus:
    svc, allowed = sl.metadata.service, 1 - slo.objective
    try:
        health = await signal_health(prom, sl, slo)
        integrity = classify(health)
        e30 = await prom.scalar(f"bg:error_ratio:increase{sl.spec.window}{_sel(svc, slo.name)}")
        e1h = await prom.scalar(f"bg:error_ratio:rate1h{_sel(svc, slo.name)}")
        alerts = await prom.query(f'ALERTS{{alertstate="firing",service="{svc}"}}')
    except PrometheusUnavailable:
        # Blind is not healthy: the gate treats it as STALE and the service's failure_mode decides.
        return SloStatus(svc, slo.name, Integrity.STALE, SliState.UNKNOWN, None, None, False, False, 0.0)
    severities = {a["metric"].get("severity") for a in alerts
                  if a["metric"].get("slo") in (None, slo.name)}       # SLIIntegrityLost has no slo label
    page, ticket = "page" in severities, "ticket" in severities
    remaining = None if e30 is None else 100.0 * (1.0 - e30 / allowed)
    state = sli_state(integrity, remaining, page, ticket, sl.spec.gate.reserve_pct)
    return SloStatus(svc, slo.name, integrity, state, remaining,
                     None if e1h is None else e1h / allowed, page, ticket, health.events_last_hour)


async def service_statuses(prom: PrometheusClient, specs: list[ServiceLevel]) -> list[SloStatus]:
    return [await slo_status(prom, sl, slo) for sl in specs for slo in sl.spec.slos]
