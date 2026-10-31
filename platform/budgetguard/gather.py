"""Gather the raw facts for one deploy decision (W5.6 step 4). Live signals from Prometheus (3 s
timeout, breaker), history from TimescaleDB, concurrently.

One GateInput covers every SLO of the service:
  integrity        the worst across the SLOs (no_data > stale > low_traffic > ok)
  remaining_*_pct  the most constrained SLO; buckets first, the increase30d rule as the fallback
                   (Rev 2 §11.2, option C with B). source="raw" asks Prometheus for increase()
                   over 30 d on every evaluation instead (option A): same numbers, kept only to
                   measure the scaling decision (W8.7, load/merge-storm.js). remaining_self
                   uses self_bad where the SLI has an origin label and all bad events where it
                   does not (latency): no attribution data = self.
  page_firing      any SLO's page alert; self_share_1h is the largest self share among the paging
                   SLOs, 1.0 for an SLI without origin labels, so a latency page is never waved
                   through as "someone else's".
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .budget import Counts, budget_remaining_pct
from .db import DB_DOWN
from .integrations.prometheus import PrometheusClient, PrometheusUnavailable
from .integrity import Integrity
from .rulegen.spec import ServiceLevel, Slo
from .signals import SloStatus, slo_status

WORST = [Integrity.OK, Integrity.LOW_TRAFFIC, Integrity.STALE, Integrity.NO_DATA]


@dataclass(frozen=True, slots=True)
class Signals:
    integrity: Integrity
    remaining_all_pct: float | None
    remaining_self_pct: float | None
    page_firing: bool
    self_share_1h: float | None
    constraining_slo: str            # the SLO whose budget is lowest: the one an ETA replays


@dataclass(frozen=True, slots=True)
class History:
    deploys: int
    failures: int
    median_failure_cost_pct: float | None


WINDOWS = text("""
SELECT o.name AS slo, sum(b.total) AS total, sum(b.bad) AS bad, sum(b.self_bad) AS self_bad
FROM sli_buckets b JOIN slos o ON o.id = b.slo_id JOIN services s ON s.id = o.service_id
WHERE s.name = :service AND o.active AND b.bucket_start >= :since AND b.bucket_start < :now
GROUP BY o.name
""")

# Query 4.7 with the leak rule: a deploy counts only once its 60-minute window had closed by `now`.
HISTORY = text("""
SELECT count(*) FILTER (WHERE d.is_change_failure IS NOT NULL) AS deploys,
       count(*) FILTER (WHERE d.is_change_failure) AS failures,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY d.change_failure_budget_pct)
           FILTER (WHERE d.is_change_failure) AS median_failure_cost_pct
FROM deployments d JOIN services s ON s.id = d.service_id
WHERE s.name = :service AND d.started_at >= :since
  AND d.finished_at + INTERVAL '60 minutes' <= :now
""")


async def self_share(prom: PrometheusClient, service: str, slo: Slo) -> float | None:
    if not slo.sli.self_bad:
        return 1.0
    sel = f'{{service="{service}",slo="{slo.name}"}}'
    return await prom.scalar(f"sum(bg:self_error_ratio:rate1h{sel}) / sum(bg:error_ratio:rate1h{sel})")


async def bucket_windows(engine: AsyncEngine, service: str,
                         now: datetime) -> dict[str, tuple[float, float, float]]:
    async with engine.connect() as c:
        rows = (await c.execute(WINDOWS, {"service": service, "since": now - timedelta(days=30),
                                          "now": now})).all()
    return {r.slo: (r.total, r.bad, r.self_bad) for r in rows}


async def raw_windows(prom: PrometheusClient, sl: ServiceLevel) -> dict[str, tuple[float, float, float]]:
    """Option A: 30-day totals straight from the raw counters (172,800 samples per series)."""
    out = {}
    for slo in sl.spec.slos:
        w = f"[{sl.spec.window}]"
        total = await prom.scalar(f"sum(increase({slo.sli.total}{w}))")
        if slo.sli.type == "ratio":
            bad = await prom.scalar(f"sum(increase({slo.sli.bad}{w}))")
        else:
            good = await prom.scalar(f"sum(increase({slo.sli.good}{w}))")
            bad = None if total is None or good is None else max(total - good, 0.0)
        own = await prom.scalar(f"sum(increase({slo.sli.self_bad}{w}))") if slo.sli.self_bad else bad
        if total is not None and bad is not None:
            out[slo.name] = (total, bad, bad if own is None else own)
    return out


async def history(engine: AsyncEngine, service: str, now: datetime) -> History:
    async with engine.connect() as c:
        r = (await c.execute(HISTORY, {"service": service, "since": now - timedelta(days=180),
                                       "now": now})).one()
    return History(int(r.deploys), int(r.failures),
                   None if r.median_failure_cost_pct is None else float(r.median_failure_cost_pct))


def combine(sl: ServiceLevel, statuses: dict[str, SloStatus],
            windows: dict[str, tuple[float, float, float]] | None,
            shares: dict[str, float | None]) -> Signals:
    """Pure: fold per-SLO signals into one service-level Signals. `windows` None = DB unavailable."""
    integrity = max((statuses[s.name].integrity for s in sl.spec.slos), key=WORST.index)
    rem_all: list[tuple[float, str]] = []
    rem_self: list[tuple[float, str]] = []
    for slo in sl.spec.slos:
        st, w = statuses[slo.name], (windows or {}).get(slo.name)
        all_pct = self_pct = None
        if w is not None:
            total, bad, self_bad = w
            all_pct = budget_remaining_pct(Counts(bad=bad, total=total), slo.objective)
            own = self_bad if slo.sli.self_bad else bad
            self_pct = budget_remaining_pct(Counts(bad=own, total=total), slo.objective)
        if all_pct is None:                      # no buckets: the recording rule (all errors only)
            all_pct = self_pct = st.remaining_pct
        if all_pct is not None:
            rem_all.append((all_pct, slo.name))
        if self_pct is not None:
            rem_self.append((self_pct, slo.name))
    paging = [s for s in sl.spec.slos if statuses[s.name].page_firing]
    share_values = [v for s in paging if (v := shares.get(s.name)) is not None]
    constraining = min(rem_self or rem_all or [(0.0, sl.spec.slos[0].name)])[1]
    return Signals(
        integrity=integrity,
        remaining_all_pct=min(rem_all)[0] if len(rem_all) == len(sl.spec.slos) else None,
        remaining_self_pct=min(rem_self)[0] if len(rem_self) == len(sl.spec.slos) else None,
        page_firing=bool(paging),
        self_share_1h=max(share_values) if share_values else None,
        constraining_slo=constraining,
    )


async def gather_signals(prom: PrometheusClient, engine: AsyncEngine | None, sl: ServiceLevel,
                         now: datetime, source: Literal["buckets", "raw"] = "buckets") -> Signals:
    """Prometheus and TimescaleDB concurrently. A database outage falls back to the rule; a
    Prometheus outage makes every SLO STALE inside slo_status (never healthy)."""
    service = sl.metadata.service

    async def windows() -> dict[str, tuple[float, float, float]] | None:
        if source == "raw":
            try:
                return await raw_windows(prom, sl)
            except PrometheusUnavailable:
                return None
        if engine is None:
            return None
        try:
            return await bucket_windows(engine, service, now)
        except DB_DOWN:
            return None

    async with asyncio.TaskGroup() as tg:
        status_tasks = {slo.name: tg.create_task(slo_status(prom, sl, slo)) for slo in sl.spec.slos}
        win_task = tg.create_task(windows())
    statuses = {name: t.result() for name, t in status_tasks.items()}
    shares: dict[str, float | None] = {}
    for slo in sl.spec.slos:
        if statuses[slo.name].page_firing:
            try:
                shares[slo.name] = await self_share(prom, service, slo)
            except PrometheusUnavailable:
                shares[slo.name] = 1.0           # cannot tell: treat the page as self-inflicted
    return combine(sl, statuses, win_task.result(), shares)
