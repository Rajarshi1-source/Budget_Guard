"""W4.9 integration tests against a real TimescaleDB (TSL image, the tag in .env.versions).

Skipped unless BG_TEST_DATABASE_URL points at a database the test may migrate, as its owner:
  CI: the `integration` job's service container. Locally (WSL Ubuntu-D):
    docker run -d --name bg-itest -p 5433:5432 -e POSTGRES_DB=budgetguard -e POSTGRES_USER=bg \
      -e POSTGRES_PASSWORD=itest timescale/timescaledb:2.31.0-pg18
    export BG_TEST_DATABASE_URL=postgresql+asyncpg://bg:itest@127.0.0.1:5433/budgetguard
    uv run pytest tests/integration
The plan names testcontainers; Docker runs only inside WSL here, so a service container plus this
variable gives the same coverage on both sides (docs/decisions.md, W4).
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from budgetguard.pgstore import PgStore
from budgetguard.rulegen.spec import load_dir
from budgetguard.slo_registry import load_slos, sync_slos
from budgetguard.workers.snapshotter import BUCKET, SETTLE, aligned_end, tick

URL = os.environ.get("BG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="BG_TEST_DATABASE_URL not set")
PLATFORM = Path(__file__).resolve().parents[2]
SPECS = load_dir(PLATFORM.parent / "slo")
NOW = aligned_end(datetime.now(UTC)) - timedelta(days=2)        # clear of the live snapshotter's window


def as_role(role: str, password: str) -> str:
    assert URL
    parts = urlsplit(URL)
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit(parts._replace(netloc=f"{role}:{password}@{host}"))


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    env = {**os.environ, "BG_DATABASE_URL": URL or ""}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=PLATFORM, env=env, check=True)


@pytest.fixture
async def owner() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(URL or "")
    yield engine
    await engine.dispose()


async def test_migrations_create_the_schema(owner: AsyncEngine) -> None:
    async with owner.connect() as c:
        hyper = (await c.execute(text(
            "SELECT hypertable_name FROM timescaledb_information.hypertables"))).scalars()
        caggs = (await c.execute(text(
            "SELECT view_name, materialized_only FROM timescaledb_information.continuous_aggregates"))).all()
        auth = (await c.execute(text(
            "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'auth'"))).all()
        head = (await c.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert "sli_buckets" in set(hyper)
    assert {(v, m) for v, m in caggs} == {("sli_hourly", False), ("sli_daily", False)}   # real-time on
    assert {t for t, _ in auth} >= {"user", "session", "account", "verification"}
    assert {o for _, o in auth} == {"bg_auth"}
    assert head == "008"


async def test_sync_slos_is_idempotent(owner: AsyncEngine) -> None:
    async with owner.begin() as c:
        first = await sync_slos(c, SPECS)
    async with owner.begin() as c:
        second = await sync_slos(c, SPECS)
        slos = await load_slos(c, SPECS)
    assert first.ids == second.ids and second.inserted == 0
    assert len(slos) == sum(len(sl.spec.slos) for sl in SPECS)


class FakeProm:
    """6,000 requests and 6 errors (2 self-inflicted) in every 5-minute bucket."""
    calls = 0

    async def range_values(self, expr: str, start: datetime, end: datetime,
                           step_s: int) -> dict[float, float]:
        FakeProm.calls += 1
        value = 6.0 if 'status=~"5.."' in expr else 6000.0
        if 'error_origin="self"' in expr:
            value = 2.0
        t, out = start, {}
        while t <= end:
            out[t.timestamp()] = value
            t += BUCKET
        return out


async def test_snapshot_upsert_is_idempotent_and_reads_back(owner: AsyncEngine) -> None:
    async with owner.connect() as c:
        slos = await load_slos(c, SPECS)
    prom = FakeProm()

    async def clear() -> None:                          # before and after, so a rerun starts clean
        async with owner.begin() as c:
            await c.execute(text("DELETE FROM sli_buckets WHERE bucket_start >= :a AND bucket_start < :b"),
                            {"a": NOW - timedelta(days=1), "b": NOW})

    await clear()
    try:
        wall = NOW + SETTLE                                               # tick() settles the newest bucket
        first = await tick(owner, prom, slos, wall, timedelta(hours=1))     # type: ignore[arg-type]
        calls = FakeProm.calls
        second = await tick(owner, prom, slos, wall, timedelta(hours=1))    # type: ignore[arg-type]
        assert first == 12 * len(slos) and second == 0
        assert FakeProm.calls == calls                                    # nothing missing: no queries
        store = PgStore(owner)
        total, bad, self_bad = (await store.slo_windows(NOW))[("orders-service", "availability")]
        assert (bad / total, self_bad / total) == (pytest.approx(0.001), pytest.approx(2 / 6000))
        eta = await store.eta_buckets("orders-service", "availability", NOW)
        assert len(eta) == 8640 and eta[-1].start == NOW - BUCKET and eta[-1].total == 6000
    finally:
        await clear()


async def test_bg_grafana_reads_the_allowlist_only(owner: AsyncEngine) -> None:
    async with owner.begin() as c:
        await c.execute(text("ALTER ROLE bg_grafana LOGIN PASSWORD 'itest-grafana'"))
    grafana = create_async_engine(as_role("bg_grafana", "itest-grafana"))
    try:
        async with grafana.connect() as c:
            assert (await c.execute(text("SHOW statement_timeout"))).scalar_one() == "5s"
            await c.execute(text("SELECT count(*) FROM sli_hourly"))
        for denied in ("SELECT reason FROM overrides", 'SELECT * FROM auth."user"',
                       "SELECT * FROM auth.session", "INSERT INTO services (name, owner) VALUES ('x', 'y')"):
            async with grafana.connect() as c:
                with pytest.raises(Exception, match="permission denied|read-only transaction"):
                    await c.execute(text(denied))
    finally:
        await grafana.dispose()


async def test_bg_app_cannot_touch_auth_or_ddl(owner: AsyncEngine) -> None:
    async with owner.begin() as c:
        await c.execute(text("ALTER ROLE bg_app LOGIN PASSWORD 'itest-app'"))
    app = create_async_engine(as_role("bg_app", "itest-app"))
    try:
        for denied in ('SELECT * FROM auth."user"', "CREATE TABLE public.x (i int)"):
            async with app.connect() as c:
                with pytest.raises(Exception, match="permission denied"):
                    await c.execute(text(denied))
    finally:
        await app.dispose()
