"""W6 integration: pricing history from real rows, the change-failure timer, the deploy recorder, the
incident-fix lane and the freeze_impact view, against TimescaleDB. Skipped without
BG_TEST_DATABASE_URL (see test_db.py for the container command)."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from budgetguard.budget import budget_spent
from budgetguard.changes import PullRequest
from budgetguard.gate import Lane, Verdict, evaluate
from budgetguard.gather import history
from budgetguard.integrity import Integrity
from budgetguard.lanes import incident_fix_proof
from budgetguard.policy import Policy
from budgetguard.pricing import ChangeFeatures
from budgetguard.records import DeployFacts, to_gate_input
from budgetguard.rulegen.spec import load_dir
from budgetguard.slo_registry import sync_slos
from budgetguard.workers.deploy_events import record, settle

URL = os.environ.get("BG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="BG_TEST_DATABASE_URL not set")
PLATFORM = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=PLATFORM,
                   env={**os.environ, "BG_DATABASE_URL": URL or ""}, check=True)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    e = create_async_engine(URL or "")
    async with e.begin() as c:
        await sync_slos(c, load_dir(PLATFORM.parent / "slo"))
        await c.execute(text("INSERT INTO policy_versions (version) VALUES ('itest-policy') "
                             "ON CONFLICT DO NOTHING"))
    yield e
    await e.dispose()


async def fixture_service(engine: AsyncEngine) -> str:
    """A throwaway service so other tests' rows never change these counts."""
    name = f"fixture-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO services (name, owner) VALUES (:n, 'itest')"), {"n": name})
    return name


async def deploy(engine: AsyncEngine, service: str, finished: datetime, failure: bool | None,
                 cost: float | None = None, status: str = "success") -> int:
    async with engine.begin() as c:
        return int((await c.execute(text("""
            INSERT INTO deployments (service_id, sha, started_at, finished_at, status, is_change_failure,
                                     change_failure_budget_pct)
            SELECT id, :sha, CAST(:f AS timestamptz) - INTERVAL '10 minutes', :f, :st, :fail, :cost
            FROM services WHERE name = :s
            RETURNING id"""), {"sha": uuid.uuid4().hex.ljust(40, "0"), "f": finished, "st": status,
                               "fail": failure, "cost": cost, "s": service})).scalar_one())


# ---------------------------------------------------------------- W6.2 + the §D table from real rows
async def test_history_and_the_worked_example_come_from_rows(engine: AsyncEngine) -> None:
    svc = await fixture_service(engine)
    old = NOW - timedelta(days=3)
    for i, cost in enumerate((3.0, 4.2, 6.0)):
        await deploy(engine, svc, old + timedelta(hours=i), True, cost)
    for i in range(27):
        await deploy(engine, svc, old + timedelta(hours=10 + i), False, 0.1)
    await deploy(engine, svc, NOW - timedelta(minutes=30), True, 50.0)     # window still open: leak rule
    h = await history(engine, svc, NOW)
    assert (h.deploys, h.failures, h.median_failure_cost_pct) == (30, 3, pytest.approx(4.2))

    small, big = ChangeFeatures(40, False, False, False), ChangeFeatures(900, True, False, False)
    rows = [(18.0, small, None, 0.42, Verdict.ADMIT), (7.0, small, None, 0.42, Verdict.ADMIT),
            (7.0, big, None, 1.26, Verdict.BLOCK), (7.0, big, Lane.ROLLBACK, 0.0, Verdict.ADMIT)]
    for remaining, features, lane, price, verdict in rows:
        facts = DeployFacts(case_id="x", service=svc, at=NOW, integrity=Integrity.OK,
                            remaining_all_pct=remaining, remaining_self_pct=remaining, page_firing=False,
                            self_share_1h=None, lane=lane,
                            features=features, prior_deploys=h.deploys, prior_failures=h.failures,
                            median_failure_cost_pct=h.median_failure_cost_pct)
        d = evaluate(to_gate_input(facts, Policy("itest-policy")))
        assert (d.verdict, d.price_pct) == (verdict, pytest.approx(price)), (remaining, features, lane)


# ---------------------------------------------------------------- W6.3 change-failure timer
async def buckets(engine: AsyncEngine, start: datetime, total: float, bad: float, self_bad: float) -> None:
    async with engine.begin() as c:
        slo_id = (await c.execute(text(
            "SELECT o.id FROM slos o JOIN services s ON s.id = o.service_id "
            "WHERE s.name = 'orders-service' AND o.name = 'availability'"))).scalar_one()
        for i in range(12):
            await c.execute(text("""
                INSERT INTO sli_buckets (slo_id, bucket_start, total, bad, self_bad)
                VALUES (:o, :t, :total, :bad, :self) ON CONFLICT (slo_id, bucket_start) DO UPDATE
                SET total = EXCLUDED.total, bad = EXCLUDED.bad, self_bad = EXCLUDED.self_bad"""),
                {"o": slo_id, "t": start + i * timedelta(minutes=5), "total": total, "bad": bad,
                 "self": self_bad})


async def page(engine: AsyncEngine, at: datetime) -> None:
    async with engine.begin() as c:
        await c.execute(text("""
            INSERT INTO incidents (service_id, fingerprint, alertname, slo, incident_type, severity,
                                   started_at)
            SELECT id, :fp, 'ErrorBudgetBurnPage', 'availability', 'availability_burn', 'page', :t
            FROM services WHERE name = 'orders-service'"""), {"fp": uuid.uuid4().hex, "t": at})


async def test_self_inflicted_burn_after_a_deploy_is_a_change_failure_priced_like_budget_spent(
        engine: AsyncEngine) -> None:
    t0 = (NOW - timedelta(days=40 + uuid.uuid4().int % 100)).replace(minute=0, second=0)   # outside 30 d
    await buckets(engine, t0, total=6000, bad=120, self_bad=120)            # 2% self errors: burn 20
    await page(engine, t0 + timedelta(minutes=12))
    dep = await deploy(engine, "orders-service", t0, None)
    settled = dict(await settle(engine, NOW, 0.5))
    assert settled[dep] is True
    async with engine.connect() as c:
        cost = (await c.execute(text("SELECT change_failure_budget_pct FROM deployments WHERE id = :i"),
                                {"i": dep})).scalar_one()
    assert float(cost) == pytest.approx(100 * budget_spent(20.0, 1.0), rel=1e-4)    # 2.78% of 30 days


async def test_a_dependency_outage_after_a_deploy_is_not_its_change_failure(engine: AsyncEngine) -> None:
    t0 = (NOW - timedelta(days=150 + uuid.uuid4().int % 100)).replace(minute=0, second=0)
    await buckets(engine, t0, total=6000, bad=600, self_bad=12)             # 98% of errors are auth's
    await page(engine, t0 + timedelta(minutes=5))
    dep = await deploy(engine, "orders-service", t0, None)
    assert dict(await settle(engine, NOW, 0.5))[dep] is False


async def test_open_window_is_not_settled(engine: AsyncEngine) -> None:
    dep = await deploy(engine, "orders-service", NOW - timedelta(minutes=20), None)
    assert dep not in dict(await settle(engine, NOW, 0.5))


# ---------------------------------------------------------------- W6.3 recorder
async def test_deployment_status_closes_the_admitted_deployment(engine: AsyncEngine) -> None:
    dep_id = uuid.uuid4().int % 10**12
    async with engine.begin() as c:
        decision = (await c.execute(text("""
            INSERT INTO decisions (service_id, github_delivery, github_deployment_id, sha, policy_version,
                                   inputs_hash, inputs, verdict, reason_code, canary_profile)
            SELECT id, :d, :dep, :sha, 'itest-policy', 'h', '{}', 'admit', 'WITHIN_BUDGET', '{25,100}'
            FROM services WHERE name = 'orders-service' RETURNING id"""),
            {"d": uuid.uuid4().hex, "dep": dep_id, "sha": "f" * 40})).scalar_one()
        await c.execute(text("""
            INSERT INTO deployments (service_id, decision_id, sha, started_at, status)
            SELECT id, :dec, :sha, now(), 'in_progress' FROM services WHERE name = 'orders-service'"""),
            {"dec": decision, "sha": "f" * 40})
    async with engine.begin() as c:
        out = await record(c, {"event": "deployment_status", "deployment_id": dep_id, "state": "failure",
                               "at": "2026-10-31T12:00:00Z"})
        again = await record(c, {"event": "deployment_status", "deployment_id": dep_id, "state": "success",
                                 "at": "2026-10-31T12:05:00Z"})
        row = (await c.execute(text("SELECT status, is_change_failure, finished_at FROM deployments "
                                    "WHERE decision_id = :d"), {"d": decision})).one()
    assert out.startswith("failure") and again == "no in-progress deployment for it"
    assert (row.status, row.is_change_failure) == ("failure", True)


# ---------------------------------------------------------------- W6.4 incident-fix lane
class Prom:
    def __init__(self, firing: bool) -> None:
        self.firing, self.queries = firing, []

    async def query(self, expr: str) -> list[dict[str, Any]]:
        self.queries.append(expr)
        return [{"metric": {}, "value": [0, "1"]}] if self.firing else []


async def open_incident(engine: AsyncEngine, service: str) -> int:
    async with engine.begin() as c:
        return int((await c.execute(text("""
            INSERT INTO incidents (service_id, fingerprint, alertname, slo, incident_type, severity,
                                   started_at)
            SELECT id, :fp, 'ErrorBudgetBurnPage', 'availability', 'availability_burn', 'page', now()
            FROM services WHERE name = :s RETURNING id"""),
            {"fp": uuid.uuid4().hex, "s": service})).scalar_one())


async def test_incident_fix_needs_an_open_incident_here_and_a_firing_alert(engine: AsyncEngine) -> None:
    mine, other = await open_incident(engine, "orders-service"), await open_incident(engine, "auth-service")
    pr = PullRequest(5, f"Fixes BG-INC-{mine}: retry the payment call", "alice", 120, None)
    async with engine.connect() as c:
        ok = await incident_fix_proof(c, Prom(True), "orders-service", pr)
        quiet = await incident_fix_proof(c, Prom(False), "orders-service", pr)
        elsewhere = await incident_fix_proof(c, Prom(True), "orders-service",
                                             PullRequest(5, f"Fixes BG-INC-{other}", "alice", 10, None))
    assert ok.lane is Lane.INCIDENT_FIX and ok.evidence["alertname"] == "ErrorBudgetBurnPage"
    assert quiet.lane is None and quiet.evidence["denied"] == "alert is not firing"         # T-6
    assert elsewhere.lane is None and "no open incident" in elsewhere.evidence["denied"]


# ---------------------------------------------------------------- W6.6 freeze_impact
async def test_freeze_impact_partitions_time_between_decisions(engine: AsyncEngine) -> None:
    svc = await fixture_service(engine)
    t0 = (NOW - timedelta(days=2)).replace(hour=10, minute=0, second=0)
    async with engine.begin() as c:
        for offset, verdict in ((0, "block"), (10, "block"), (30, "admit")):
            await c.execute(text("""
                INSERT INTO decisions (service_id, github_delivery, sha, policy_version, inputs_hash, inputs,
                                       verdict, reason_code, canary_profile, decided_at)
                SELECT id, :d, :sha, 'itest-policy', 'h', '{}', :v, 'X', '{100}', :t
                FROM services WHERE name = :s"""),
                {"d": uuid.uuid4().hex, "sha": "a" * 40, "v": verdict, "t": t0 + timedelta(minutes=offset),
                 "s": svc})
        row = (await c.execute(text("SELECT blocked_deploys, admitted_deploys, freeze_minutes "
                                    "FROM freeze_impact WHERE service = :s"), {"s": svc})).one()
    assert (row.blocked_deploys, row.admitted_deploys, float(row.freeze_minutes)) == (2, 1, 30.0)
