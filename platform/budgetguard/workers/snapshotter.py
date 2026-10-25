"""Snapshotter (W4.4, budgetguard-data-layer §3): Prometheus increase() -> 5-minute sli_buckets rows.

    python -m budgetguard.workers.snapshotter [--backfill-hours 6] [--once]

Every tick it fills every *missing* aligned bucket of every active SLO in the backfill window, so a
restart or an outage shorter than Prometheus' 35-day retention heals itself. Existing rows are never
rewritten: once a bucket is older than retention, Prometheus would answer "no data" for it, and a
rewrite would erase good history. One range query per series and window keeps a 30-day backfill
to a handful of calls.

One writer across replicas: a session-level PostgreSQL advisory lock instead of the Kubernetes
Lease Rev 2 names (same guarantee, works in compose too, needs no RBAC). If the leader's
connection dies the lock is released and a standby takes over.

When Prometheus is unreachable the tick writes nothing (an outage of the observer is not "no data"
for the service); the next successful tick backfills.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from prometheus_client import Gauge, start_http_server
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .. import redact
from ..config import get_core_settings
from ..integrations.prometheus import PrometheusClient, PrometheusUnavailable
from ..rulegen.spec import load_dir
from ..slo_registry import SloQueries, load_slos

log = logging.getLogger("budgetguard.snapshotter")

BUCKET = timedelta(minutes=5)
SETTLE = timedelta(seconds=45)      # 3 scrape intervals: the bucket's last samples must have landed
LEADER_LOCK = 724_001               # pg advisory lock id: one writer across replicas
TICK_S = 60

LEADER = Gauge("bg_snapshotter_leader", "1 while this replica holds the snapshotter lock")
LAST_BUCKET = Gauge("bg_snapshotter_last_bucket_timestamp_seconds",
                    "Start of the newest bucket written (unix seconds)")

UPSERT = text("""
INSERT INTO sli_buckets (slo_id, bucket_start, total, bad, self_bad, integrity)
VALUES (:slo_id, :bucket_start, :total, :bad, :self_bad, :integrity)
ON CONFLICT (slo_id, bucket_start) DO UPDATE
SET total = EXCLUDED.total, bad = EXCLUDED.bad, self_bad = EXCLUDED.self_bad,
    integrity = EXCLUDED.integrity
""")

MISSING = text("""
SELECT g.t AS bucket_start
FROM generate_series(CAST(:first AS timestamptz), CAST(:last AS timestamptz), INTERVAL '5 minutes') AS g(t)
LEFT JOIN sli_buckets b ON b.slo_id = :slo_id AND b.bucket_start = g.t
WHERE b.slo_id IS NULL
ORDER BY g.t
""")


def aligned_end(now: datetime) -> datetime:
    """End of the newest bucket that is complete and settled: buckets are [start, start + 5m)."""
    settled = (now - SETTLE).replace(second=0, microsecond=0)
    return settled - timedelta(minutes=settled.minute % 5)


@dataclass(frozen=True, slots=True)
class Row:
    slo_id: int
    bucket_start: datetime
    total: float
    bad: float
    self_bad: float
    integrity: str

    def params(self) -> dict[str, Any]:
        return {"slo_id": self.slo_id, "bucket_start": self.bucket_start, "total": self.total,
                "bad": self.bad, "self_bad": self.self_bad, "integrity": self.integrity}


def bucket_row(slo: SloQueries, start: datetime, total: float | None, bad: float | None,
               good: float | None, self_bad: float | None) -> Row:
    """One bucket from raw increases. Absent numerators are zero when the denominator exists, the
    same zero-fill the recording rules use, so the 30-day sums agree with bg:error_ratio:increase30d:
    a ratio SLI with no 5xx series had no errors; a latency SLI with no `good` series (every
    notification failed, say) had no good events, so every event was bad."""
    if total is None:
        return Row(slo.id, start, 0.0, 0.0, 0.0, "no_data")
    total = max(total, 0.0)
    err = (bad or 0.0) if slo.type == "ratio" else total - (good or 0.0)
    err = min(max(err, 0.0), total)                # extrapolation noise cannot make bad > total
    own = min(max(self_bad or 0.0, 0.0), err)
    per_hour = total * (timedelta(hours=1) / BUCKET)
    integrity = "ok" if per_hour >= slo.min_events_per_hour and total > 0 else "low_traffic"
    return Row(slo.id, start, total, err, own, integrity)


async def snapshot_range(prom: PrometheusClient, slo: SloQueries,
                         starts: list[datetime]) -> list[Row]:
    """Rows for the given bucket starts. A bucket [s, s+5m) is increase(...[5m]) evaluated at s+5m."""
    if not starts:
        return []
    first, last = starts[0] + BUCKET, starts[-1] + BUCKET
    step = int(BUCKET.total_seconds())

    async def series(selector: str | None) -> dict[float, float] | None:
        if selector is None:
            return None
        return await prom.range_values(f"sum(increase({selector}[5m]))", first, last, step)

    total = await prom.range_values(f"sum(increase({slo.total}[5m]))", first, last, step)
    bad = await series(slo.bad if slo.type == "ratio" else None)
    good = await series(slo.good if slo.type == "latency_bucket" else None)
    self_bad = await series(slo.self_bad)
    rows = []
    for s in starts:
        ts = (s + BUCKET).timestamp()
        rows.append(bucket_row(slo, s, total.get(ts),
                               bad.get(ts) if bad is not None else None,
                               good.get(ts) if good is not None else None,
                               self_bad.get(ts) if self_bad is not None else None))
    return rows


async def missing_starts(conn: AsyncConnection, slo_id: int, end: datetime,
                         backfill: timedelta) -> list[datetime]:
    result = await conn.execute(MISSING, {"slo_id": slo_id, "first": end - backfill, "last": end - BUCKET})
    return [r.bucket_start for r in result]


async def tick(engine: AsyncEngine, prom: PrometheusClient, slos: dict[int, SloQueries],
               now: datetime, backfill: timedelta) -> int:
    """Fill every missing bucket in the window; returns the number of rows written."""
    end = aligned_end(now)
    written = 0
    for slo in slos.values():
        async with engine.begin() as conn:
            starts = await missing_starts(conn, slo.id, end, backfill)
            rows = await snapshot_range(prom, slo, starts)
            if rows:
                await conn.execute(UPSERT, [r.params() for r in rows])
                written += len(rows)
    if written:
        LAST_BUCKET.set((end - BUCKET).timestamp())
    return written


async def run(backfill: timedelta, once: bool, metrics_port: int | None) -> None:
    from ..db import make_engine

    settings = get_core_settings()
    if metrics_port is not None:
        start_http_server(metrics_port)
    engine = make_engine()
    try:
        async with engine.connect() as conn:
            slos = await load_slos(conn, load_dir(settings.slo_dir))
        async with httpx.AsyncClient() as http, engine.connect() as lock_conn:
            prom = PrometheusClient(settings.prometheus_url, http, timeout_s=30.0)  # range queries
            while not (await lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                               {"k": LEADER_LOCK})).scalar():
                if once:
                    log.info("another replica holds the lock; nothing to do")
                    return
                await asyncio.sleep(15)                           # standby replica
            LEADER.set(1)
            log.info("leader: snapshotting %d slos, backfill %s", len(slos), backfill)
            while True:
                try:
                    n = await tick(engine, prom, slos, datetime.now(UTC), backfill)
                    log.info("wrote %d buckets", n)
                except PrometheusUnavailable as e:
                    log.warning("prometheus unavailable (%s); will backfill next tick", e)
                if once:
                    return
                await asyncio.sleep(TICK_S)
                await lock_conn.execute(text("SELECT 1"))         # a dead lock connection ends the leader
    finally:
        LEADER.set(0)
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="snapshotter")
    ap.add_argument("--backfill-hours", type=float, default=6.0,
                    help="fill missing buckets this far back (max ~35 days: Prometheus retention)")
    ap.add_argument("--once", action="store_true", help="one tick, then exit")
    ap.add_argument("--metrics-port", type=int, default=None)
    a = ap.parse_args(argv)
    redact.install()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)       # one line per range query is noise
    asyncio.run(run(timedelta(hours=a.backfill_hours), a.once, a.metrics_port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
