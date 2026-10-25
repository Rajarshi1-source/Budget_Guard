"""PostgreSQL implementation of Store. SQL mirrors budgetguard-data-layer/references/schema-and-queries.md.
Covered by an integration test against the TimescaleDB image (testcontainers), not by unit tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .eta import BUCKET, Bucket
from .store import DecisionRow, IncidentEvent, SloKey

DECISION_SQL = """
SELECT d.id, s.name AS service, d.sha, d.github_run_id, d.verdict, d.reason_code,
       d.price_pct::float8 AS price_pct, d.affordable_pct::float8 AS affordable_pct,
       d.canary_profile, d.policy_version, d.inputs_hash, d.inputs, d.decided_at,
       d.inputs -> 'github' ->> 'repo' AS repo,
       (d.inputs -> 'github' ->> 'installation_id')::bigint AS installation_id,
       (d.verdict = 'block'
        AND d.decided_at > now() - INTERVAL '30 days'
        AND NOT EXISTS (SELECT 1 FROM overrides o WHERE o.decision_id = d.id)) AS overridable,
       ov.approver AS override_approver, ov.reason AS override_reason, ov.created_at AS override_at
FROM decisions d JOIN services s ON s.id = d.service_id
LEFT JOIN LATERAL (SELECT o.approver, o.reason, o.created_at FROM overrides o
                   WHERE o.decision_id = d.id ORDER BY o.created_at DESC LIMIT 1) ov ON true
"""


class PgStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @classmethod
    async def connect(cls, url: str) -> PgStore:
        return cls(create_async_engine(url, pool_size=10, max_overflow=5, pool_pre_ping=True))

    async def close(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> None:
        async with self.engine.connect() as c:
            await c.execute(text("SELECT 1"))

    @staticmethod
    def _row(m: Any) -> DecisionRow:
        return DecisionRow(id=m["id"], service=m["service"], sha=m["sha"], github_run_id=m["github_run_id"],
                           verdict=m["verdict"], reason_code=m["reason_code"], price_pct=m["price_pct"],
                           affordable_pct=m["affordable_pct"], canary_profile=tuple(m["canary_profile"]),
                           policy_version=m["policy_version"], inputs_hash=m["inputs_hash"],
                           inputs=m["inputs"],
                           decided_at=m["decided_at"], repo=m["repo"] or "",
                           installation_id=m["installation_id"], overridable=m["overridable"],
                           override=None if m["override_approver"] is None else {
                               "approver": m["override_approver"], "reason": m["override_reason"],
                               "created_at": m["override_at"].isoformat()})

    async def decision(self, decision_id: int) -> DecisionRow | None:
        async with self.engine.connect() as c:
            result = await c.execute(text(DECISION_SQL + " WHERE d.id = :id"), {"id": decision_id})
            m = result.mappings().first()
        return self._row(m) if m else None

    async def decision_for_run(self, service: str, run_id: int) -> DecisionRow | None:
        q = DECISION_SQL + (" WHERE s.name = :svc AND d.github_run_id = :run"
                            " ORDER BY d.decided_at DESC LIMIT 1")
        async with self.engine.connect() as c:
            m = (await c.execute(text(q), {"svc": service, "run": run_id})).mappings().first()
        return self._row(m) if m else None

    async def upsert_incident(self, e: IncidentEvent) -> None:
        async with self.engine.begin() as c:
            await c.execute(text("""
                INSERT INTO incidents (service_id, fingerprint, alertname, slo, incident_type, severity,
                                       started_at, resolved_at)
                SELECT s.id, :fp, :alertname, :slo, :itype, :sev, :started, :resolved
                FROM services s WHERE s.name = :svc
                ON CONFLICT (fingerprint, started_at)
                DO UPDATE SET resolved_at = COALESCE(EXCLUDED.resolved_at, incidents.resolved_at)
            """), {"fp": e.fingerprint, "alertname": e.alertname, "slo": e.slo, "itype": e.incident_type,
                   "sev": e.severity, "started": e.started_at, "resolved": e.resolved_at, "svc": e.service})

    async def create_override(self, decision_id: int, approver_id: int, approver: str, reason: str) -> int:
        async with self.engine.begin() as c:
            return (await c.execute(text("""
                INSERT INTO overrides (decision_id, approver_id, approver, reason)
                VALUES (:d, :i, :a, :r) RETURNING id
            """), {"d": decision_id, "i": approver_id, "a": approver, "r": reason})).scalar_one()

    async def delete_override(self, override_id: int) -> None:
        async with self.engine.begin() as c:
            await c.execute(text("DELETE FROM overrides WHERE id = :id AND consumed_by_decision_id IS NULL"),
                            {"id": override_id})

    async def open_override(self, service: str, sha: str) -> int | None:
        async with self.engine.connect() as c:
            return (await c.execute(text("""
                SELECT o.id FROM overrides o
                JOIN decisions b ON b.id = o.decision_id JOIN services s ON s.id = b.service_id
                WHERE s.name = :svc AND b.sha = :sha
                  AND o.expires_at > now() AND o.consumed_by_decision_id IS NULL
                ORDER BY o.created_at DESC LIMIT 1
            """), {"svc": service, "sha": sha})).scalar_one_or_none()

    # ------------------------------------------------------------ W4: history and dashboard reads
    async def ensure_policy_version(self, version: str) -> None:
        """decisions.policy_version is a FK: seed the running version on boot (data-layer §2)."""
        async with self.engine.begin() as c:
            await c.execute(text("INSERT INTO policy_versions (version) VALUES (:v) ON CONFLICT DO NOTHING"),
                            {"v": version})

    async def slo_windows(self, now: datetime) -> dict[SloKey, tuple[float, float, float]]:
        """Query 4.1 as raw sums; the percentages come from budget.py so there is one formula."""
        async with self.engine.connect() as c:
            rows = (await c.execute(text("""
                SELECT s.name AS service, o.name AS slo,
                       sum(b.total) AS total, sum(b.bad) AS bad, sum(b.self_bad) AS self_bad
                FROM sli_buckets b JOIN slos o ON o.id = b.slo_id JOIN services s ON s.id = o.service_id
                WHERE o.active AND b.bucket_start >= :since AND b.bucket_start < :now
                GROUP BY s.name, o.name
            """), {"since": now - timedelta(days=30), "now": now})).all()
        return {(r.service, r.slo): (r.total, r.bad, r.self_bad) for r in rows}

    async def slo_daily(self, now: datetime, days: int) -> dict[SloKey, list[tuple[date, float, float]]]:
        """Per-day sums from the buckets, not sli_daily: the newest day is never materialized (real-time
        aggregation is off by default) and a backfill older than the policy's 3-day start_offset
        reaches the caggs only on a manual refresh. About 70k rows at 30 days: cheap."""
        async with self.engine.connect() as c:
            rows = (await c.execute(text("""
                SELECT s.name AS service, o.name AS slo,
                       (time_bucket(INTERVAL '1 day', b.bucket_start) AT TIME ZONE 'UTC')::date AS day,
                       sum(b.bad) AS bad, sum(b.total) AS total
                FROM sli_buckets b JOIN slos o ON o.id = b.slo_id JOIN services s ON s.id = o.service_id
                WHERE o.active AND b.bucket_start >= :since AND b.bucket_start < :now
                GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
            """), {"since": now - timedelta(days=days), "now": now})).all()
        out: dict[SloKey, list[tuple[date, float, float]]] = {}
        for r in rows:
            out.setdefault((r.service, r.slo), []).append((r.day, r.bad, r.total))
        return out

    async def eta_buckets(self, service: str, slo: str, now: datetime) -> list[Any]:
        """Query 4.2, gaps filled with zero-total buckets so the replay deque holds exactly 30 days."""
        end = now.replace(second=0, microsecond=0)
        end -= timedelta(minutes=end.minute % 5)
        start = end - timedelta(days=30)
        async with self.engine.connect() as c:
            rows = (await c.execute(text("""
                SELECT b.bucket_start, b.bad, b.total
                FROM sli_buckets b JOIN slos o ON o.id = b.slo_id JOIN services s ON s.id = o.service_id
                WHERE s.name = :svc AND o.name = :slo AND o.active
                  AND b.bucket_start >= :start AND b.bucket_start < :end
            """), {"svc": service, "slo": slo, "start": start, "end": end})).all()
        have = {r.bucket_start: (r.bad, r.total) for r in rows}
        n = int((end - start) / BUCKET)
        return [Bucket(t, *have.get(t, (0.0, 0.0))) for t in (start + i * BUCKET for i in range(n))]

    async def open_incidents(self) -> dict[str, dict[str, Any]]:
        async with self.engine.connect() as c:
            rows = (await c.execute(text("""
                SELECT DISTINCT ON (s.name) s.name AS service, i.id, i.alertname, i.started_at
                FROM incidents i JOIN services s ON s.id = i.service_id
                WHERE i.resolved_at IS NULL
                ORDER BY s.name, (i.severity = 'page') DESC, i.started_at DESC
            """))).all()
        return {r.service: {"id": r.id, "alertname": r.alertname, "started_at": r.started_at.isoformat()}
                for r in rows}

    async def last_decisions(self) -> dict[str, dict[str, Any]]:
        async with self.engine.connect() as c:
            rows = (await c.execute(text("""
                SELECT DISTINCT ON (s.name) s.name AS service, d.id, d.verdict, d.decided_at
                FROM decisions d JOIN services s ON s.id = d.service_id
                ORDER BY s.name, d.decided_at DESC, d.id DESC
            """))).all()
        return {r.service: {"id": r.id, "verdict": r.verdict, "decided_at": r.decided_at.isoformat()}
                for r in rows}

    async def decision_page(self, service: str | None, before_id: int | None,
                            limit: int) -> list[DecisionRow]:
        """Query 4.10 with keyset pagination on id (ids grow with decided_at; no OFFSET scans)."""
        q = DECISION_SQL + """
            WHERE (CAST(:svc AS text) IS NULL OR s.name = :svc)
              AND (CAST(:before AS bigint) IS NULL OR d.id < :before)
            ORDER BY d.id DESC LIMIT :limit"""
        async with self.engine.connect() as c:
            result = await c.execute(text(q), {"svc": service, "before": before_id, "limit": limit})
            return [self._row(m) for m in result.mappings()]

    async def policy_versions(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as c:
            rows = (await c.execute(text("""
                SELECT version, created_at, backtest_report ->> 'markdown' AS backtest_markdown
                FROM policy_versions ORDER BY created_at DESC LIMIT 50
            """))).all()
        return [{"version": r.version, "created_at": r.created_at.isoformat(),
                 "backtest_markdown": r.backtest_markdown} for r in rows]
