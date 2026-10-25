"""GET /v1/services/summary: one card per service, field for field the dashboard's zod ServiceSummary
(budgetguard-nextjs-dashboard/references/dashboard-code.md). Pure assembly; I/O happens in the API.

Sources, per Rev 2 §11.2: the 30-day budget comes from TimescaleDB buckets (query 4.1), with the
bg:error_ratio:increase30d rule as the fallback when no buckets exist yet; integrity, burn and firing
alerts are live from Prometheus (signals.py); the unfreeze ETA replays the buckets (eta.py, D-5).
The card's numbers describe the first SLO in the spec (availability); state and integrity are the
worst across all of the service's SLOs, so a burning latency SLO still turns the card AT_RISK.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .budget import Counts, budget_remaining_pct
from .eta import Bucket, unfreeze_eta
from .integrity import Integrity, SliState, sli_state
from .rulegen.spec import ServiceLevel, Slo
from .signals import SloStatus

WORST_INTEGRITY = [Integrity.OK, Integrity.LOW_TRAFFIC, Integrity.STALE, Integrity.NO_DATA]
WORST_STATE = [SliState.HEALTHY, SliState.AT_RISK, SliState.UNKNOWN]


@dataclass(frozen=True, slots=True)
class Window:
    """Bucket sums for one SLO over the last 30 days."""
    total: float
    bad: float
    self_bad: float


@dataclass(frozen=True, slots=True)
class SloInputs:
    status: SloStatus                 # live, from Prometheus
    window: Window | None             # from sli_buckets; None = no buckets yet
    daily: Sequence[tuple[date, float, float]] = ()   # (day, bad, total), oldest first, up to 59 days
    eta_buckets: Sequence[Bucket] = ()               # 30 days of 5-minute buckets, only when frozen


def remaining(objective: float, w: Window | None, self_only: bool = False) -> float | None:
    if w is None:
        return None
    return budget_remaining_pct(Counts(bad=w.self_bad if self_only else w.bad, total=w.total), objective)


def rolling_daily(objective: float, daily: Sequence[tuple[date, float, float]],
                  today: date, days: int = 30) -> list[float | None]:
    """Budget remaining over the 30 days ending each of the last `days` days. Sums, never averages."""
    by_day = {d: (bad, total) for d, bad, total in daily}
    out: list[float | None] = []
    for k in range(days - 1, -1, -1):
        end = today - timedelta(days=k)
        span = [by_day.get(end - timedelta(days=i), (0.0, 0.0)) for i in range(30)]
        counts = Counts(bad=sum(b for b, _ in span), total=sum(t for _, t in span))
        r = budget_remaining_pct(counts, objective)
        out.append(None if r is None else round(r, 2))
    return out


def _freeze_reasons(sl: ServiceLevel, slo: Slo, st: SloStatus, rem: float | None) -> tuple[bool, bool]:
    """(frozen by an alert, frozen by budget) for one SLO, per the spec's gate block."""
    hard = sl.spec.gate.hard_stop_on
    by_alert = ("page" in hard and st.page_firing) or ("ticket" in hard and st.ticket_firing)
    by_budget = rem is not None and rem < sl.spec.gate.reserve_pct
    return by_alert, by_budget


def service_card(sl: ServiceLevel, inputs: dict[str, SloInputs], *, now: datetime,
                 open_incident: dict[str, Any] | None,
                 last_decision: dict[str, Any] | None) -> dict[str, Any]:
    states, integrities, etas = [], [], []
    alert_frozen = budget_frozen = False
    for slo in sl.spec.slos:
        i = inputs[slo.name]
        rem = remaining(slo.objective, i.window)
        if rem is None:
            rem = i.status.remaining_pct                          # no buckets yet: the recording rule
        integrities.append(i.status.integrity)
        states.append(sli_state(i.status.integrity, rem, i.status.page_firing, i.status.ticket_firing,
                                sl.spec.gate.reserve_pct))
        by_alert, by_budget = _freeze_reasons(sl, slo, i.status, rem)
        alert_frozen |= by_alert
        if by_budget:
            budget_frozen = True
            etas.append(unfreeze_eta(list(i.eta_buckets), slo.objective, sl.spec.gate.reserve_pct)
                        if i.eta_buckets else None)
    frozen = alert_frozen or budget_frozen
    # An ETA exists only when the budget alone holds the freeze and every frozen SLO has a projection.
    eta = None if alert_frozen or not etas or None in etas else max(e for e in etas if e is not None)

    primary = sl.spec.slos[0]
    p = inputs[primary.name]
    rem_all = remaining(primary.objective, p.window)
    return {
        "service": sl.metadata.service,
        "state": str(max(states, key=WORST_STATE.index)),
        "integrity": str(max(integrities, key=WORST_INTEGRITY.index)),
        "objective_pct": round(100 * primary.objective, 4),
        "remaining_pct": rem_all if rem_all is not None else p.status.remaining_pct,
        "remaining_self_pct": remaining(primary.objective, p.window, self_only=True),
        "burn_rate_1h": p.status.burn_rate_1h,
        "frozen": frozen,
        "unfreeze_eta": eta.isoformat() if eta else None,
        "open_incident": open_incident,
        "last_decision": last_decision,
        "daily_remaining": rolling_daily(primary.objective, p.daily, now.date()),
    }
