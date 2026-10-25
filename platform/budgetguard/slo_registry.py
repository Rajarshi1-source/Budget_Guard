"""slo/*.yaml -> `services` and `slos` rows, and slo_id -> the PromQL the snapshotter needs (W4.3).

The specs stay the source of truth; the rows exist so buckets, decisions and Grafana can join on an
id. `spec_hash` is a sha256 of the SLO's canonical JSON (objective, SLI queries, window), so a changed
objective or query shows up as drift in the row, and an SLO removed from its spec is deactivated,
never deleted: its buckets keep their history.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .rulegen.spec import ServiceLevel, Slo


@dataclass(frozen=True, slots=True)
class SloQueries:
    id: int
    service: str
    name: str
    type: Literal["ratio", "latency_bucket"]
    objective: float
    total: str
    bad: str | None                  # ratio SLIs
    good: str | None                 # latency_bucket SLIs: bad = total - good
    self_bad: str | None
    min_events_per_hour: float


def spec_hash(sl: ServiceLevel, slo: Slo) -> str:
    canonical = {"window": sl.spec.window, "objective": slo.objective, "sli": slo.sli.model_dump()}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


UPSERT_SERVICE = text("""
INSERT INTO services (name, owner, failure_mode) VALUES (:name, :owner, :failure_mode)
ON CONFLICT (name) DO UPDATE SET owner = EXCLUDED.owner, failure_mode = EXCLUDED.failure_mode
RETURNING id
""")

UPSERT_SLO = text("""
INSERT INTO slos (service_id, name, objective, window_days, spec_hash, active)
VALUES (:service_id, :name, :objective, :window_days, :spec_hash, true)
ON CONFLICT (service_id, name) DO UPDATE
SET objective = EXCLUDED.objective, window_days = EXCLUDED.window_days,
    spec_hash = EXCLUDED.spec_hash, active = true
RETURNING id, (xmax = 0) AS inserted
""")

DEACTIVATE = text("""
UPDATE slos SET active = false
WHERE active AND NOT (id = ANY(:keep))
RETURNING id
""")


@dataclass(frozen=True, slots=True)
class SyncResult:
    ids: dict[tuple[str, str], int]   # (service, slo) -> slo_id
    inserted: int
    deactivated: int


async def sync_slos(conn: AsyncConnection, specs: list[ServiceLevel]) -> SyncResult:
    """Upsert every service and SLO in `specs`; deactivate active SLOs no spec mentions. Idempotent."""
    ids: dict[tuple[str, str], int] = {}
    inserted = 0
    for sl in specs:
        svc = sl.metadata.service
        service_id = (await conn.execute(UPSERT_SERVICE, {
            "name": svc, "owner": sl.metadata.owner, "failure_mode": sl.spec.failure_mode})).scalar_one()
        for slo in sl.spec.slos:
            row = (await conn.execute(UPSERT_SLO, {
                "service_id": service_id, "name": slo.name, "objective": slo.objective,
                "window_days": int(sl.spec.window.removesuffix("d")),
                "spec_hash": spec_hash(sl, slo)})).one()
            ids[(svc, slo.name)] = row.id
            inserted += int(row.inserted)
    gone = (await conn.execute(DEACTIVATE, {"keep": list(ids.values())})).all()
    return SyncResult(ids=ids, inserted=inserted, deactivated=len(gone))


async def load_slos(conn: AsyncConnection, specs: list[ServiceLevel]) -> dict[int, SloQueries]:
    """Active slo rows joined to their spec. A row whose spec_hash differs from the spec on disk
    means `bg sync-slos` has not run since the spec changed: refuse rather than snapshot the new
    queries under an id whose objective the database still records as the old one."""
    rows = (await conn.execute(text("""
        SELECT sl.id, s.name AS service, sl.name, sl.spec_hash
        FROM slos sl JOIN services s ON s.id = sl.service_id WHERE sl.active"""))).all()
    by_key = {(r.service, r.name): r for r in rows}
    out: dict[int, SloQueries] = {}
    for sl in specs:
        for slo in sl.spec.slos:
            r = by_key.get((sl.metadata.service, slo.name))
            if r is None or r.spec_hash != spec_hash(sl, slo):
                raise LookupError(f"{sl.metadata.service}/{slo.name}: slos row missing or stale; "
                                  "run `bg sync-slos`")
            out[r.id] = SloQueries(id=r.id, service=sl.metadata.service, name=slo.name, type=slo.sli.type,
                                   objective=slo.objective, total=slo.sli.total, bad=slo.sli.bad,
                                   good=slo.sli.good, self_bad=slo.sli.self_bad,
                                   min_events_per_hour=float(sl.spec.min_events_per_hour))
    return out
