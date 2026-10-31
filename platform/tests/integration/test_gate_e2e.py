"""W5 integration: webhook -> Valkey -> gate worker -> fake GitHub callback, against real TimescaleDB
and Valkey. Skipped unless both are configured (CI `integration` job; locally in WSL Ubuntu-D):

    docker run -d --name bg-itest -p 5433:5432 -e POSTGRES_DB=budgetguard -e POSTGRES_USER=bg \\
      -e POSTGRES_PASSWORD=itest timescale/timescaledb:2.31.0-pg18
    docker run -d --name bg-itest-valkey -p 6380:6379 valkey/valkey:9.1.2
    export BG_TEST_DATABASE_URL=postgresql+asyncpg://bg:itest@127.0.0.1:5433/budgetguard
    export BG_TEST_VALKEY_URL=redis://127.0.0.1:6380/15
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from budgetguard.config import get_settings
from budgetguard.integrations.github_app import validate_job
from budgetguard.integrations.prometheus import PrometheusClient
from budgetguard.main import create_app
from budgetguard.policy import Policy
from budgetguard.records import replay
from budgetguard.rulegen.spec import load_dir
from budgetguard.slo_registry import sync_slos
from budgetguard.workers import gate_worker as gw

DB, VK = os.environ.get("BG_TEST_DATABASE_URL"), os.environ.get("BG_TEST_VALKEY_URL")
pytestmark = pytest.mark.skipif(not (DB and VK), reason="BG_TEST_DATABASE_URL / BG_TEST_VALKEY_URL not set")
PLATFORM = Path(__file__).resolve().parents[2]
SPECS = {sl.metadata.service: sl for sl in load_dir(PLATFORM.parent / "slo")}
POLICY = Policy("itest-policy")
TREE = "7" * 40                                             # services/orders-service subtree of the old build


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=PLATFORM,
                   env={**os.environ, "BG_DATABASE_URL": DB or ""}, check=True)

    async def prep() -> None:
        e = create_async_engine(DB or "")
        async with e.begin() as c:
            await sync_slos(c, list(SPECS.values()))
            await c.execute(text("INSERT INTO policy_versions (version) VALUES ('itest-policy') "
                                 "ON CONFLICT DO NOTHING"))
        await e.dispose()
    asyncio.run(prep())


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    e = create_async_engine(DB or "")
    yield e
    await e.dispose()


@pytest.fixture
async def valkey() -> AsyncIterator[Redis]:
    r = Redis.from_url(VK or "", decode_responses=True)
    await r.flushdb()
    yield r
    await r.aclose()


def burning_prometheus(page: bool) -> httpx.MockTransport:
    """orders availability: a self-inflicted page (90% of the hour's bad events are orders' own)."""
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("query", "")
        if q.startswith("ALERTS"):
            result: list[Any] = ([{"metric": {"severity": "page", "slo": "availability"}, "value": [0, "1"]}]
                                 if page and "orders-service" in q else [])
        elif "self_error_ratio" in q:
            result = [{"metric": {}, "value": [0, "0.9"]}]
        elif "increase30d" in q:
            result = [{"metric": {}, "value": [0, "0.0002"]}]
        elif "rate1h" in q:
            result = [{"metric": {}, "value": [0, "0.02"]}]
        elif "increase(" in q:
            result = [{"metric": {}, "value": [0, "36000"]}]
        else:
            result = [{"metric": {}, "value": [0, "1"]}]
        data = {"resultType": "vector", "result": result}
        return httpx.Response(200, json={"status": "success", "data": data})
    return httpx.MockTransport(handler)


class Trees:
    """Fake git trees API: sha 'rollback…' has the old subtree, anything else a new one."""
    async def get(self, path: str) -> dict[str, Any]:
        if "/git/commits/" in path:
            return {"tree": {"sha": "root-" + path.rsplit("/", 1)[1]}}
        sha = path.rsplit("/", 1)[1]
        if sha.startswith("root-"):
            return {"tree": [{"path": "services", "type": "tree", "sha": "svc-" + sha[5:]}]}
        return {"tree": [{"path": "orders-service", "type": "tree",
                          "sha": TREE if sha[4:].startswith("b" * 8) else "8" * 40}]}


def context(engine: AsyncEngine, valkey: Redis | None, posts: list[tuple[dict[str, Any], bool, str]],
            page: bool = True) -> gw.Context:
    http = httpx.AsyncClient(transport=burning_prometheus(page))

    async def post(_: httpx.AsyncClient, j: dict[str, Any], approved: bool, comment: str) -> None:
        validate_job(j)
        posts.append((j, approved, comment))

    return gw.Context(settings=get_settings(), policy=POLICY, specs=SPECS,
                      prom=PrometheusClient("http://prometheus:9090", http), engine=engine, valkey=valkey,
                      http=http, github=lambda i: Trees(), post=post)


def job(sha: str, run_id: int, deployment_id: int) -> dict[str, Any]:
    return {"service": "orders-service", "environment": "production-orders-service",
            "callback_url": f"https://api.github.com/repos/ricky/budgetguard/actions/runs/{run_id}/"
                            "deployment_protection_rule",
            "run_id": run_id, "installation_id": 99, "sha": sha, "deployment_id": deployment_id,
            "repo": "ricky/budgetguard", "delivery": str(uuid.uuid4())}


def ids() -> tuple[int, int]:
    n = uuid.uuid4().int % 10**9
    return n, n + 1


async def test_webhook_to_worker_to_callback_blocks_on_a_self_inflicted_page(engine: AsyncEngine,
                                                                             valkey: Redis) -> None:
    run_id, dep_id = ids()
    sha = "a" * 32 + uuid.uuid4().hex[:8]
    payload = {"environment": "production-orders-service", "installation": {"id": 99},
               "repository": {"full_name": "ricky/budgetguard"}, "deployment": {"id": dep_id, "sha": sha},
               "deployment_callback_url": f"https://api.github.com/repos/ricky/budgetguard/actions/runs/{run_id}/"
                                          "deployment_protection_rule"}
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"whsec", body, hashlib.sha256).hexdigest()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.valkey = Redis.from_url(VK or "", decode_responses=True)
        yield
        await app.state.valkey.aclose()

    with TestClient(create_app(lifespan_override=lifespan)) as client:
        r = client.post("/v1/github/webhook", content=body, headers={
            "X-Hub-Signature-256": sig, "X-GitHub-Event": "deployment_protection_rule",
            "X-GitHub-Delivery": f"e2e-{dep_id}", "Content-Type": "application/json"})
    assert r.json() == {"accepted": True}

    await valkey.xgroup_create(gw.STREAM, gw.GROUP, id="0", mkstream=True)
    entries = await valkey.xreadgroup(gw.GROUP, "itest", {gw.STREAM: ">"}, count=10)
    (entry_id, fields), = entries[0][1]
    posts: list[tuple[dict[str, Any], bool, str]] = []
    await gw.handle(context(engine, valkey, posts), entry_id, fields)

    assert len(posts) == 1 and posts[0][1] is False                     # GitHub told: rejected
    assert "ACTIVE_PAGE" in posts[0][2] and "/decisions/" in posts[0][2]
    async with engine.connect() as c:
        row = (await c.execute(text("SELECT id, verdict, reason_code, inputs, inputs_hash, callback_state "
                                    "FROM decisions WHERE github_deployment_id = :d"), {"d": dep_id})).one()
        deployments = (await c.execute(text("SELECT count(*) FROM deployments WHERE decision_id = :i"),
                                       {"i": row.id})).scalar_one()
    assert (row.verdict, row.reason_code, row.callback_state) == ("block", "ACTIVE_PAGE", "sent")
    assert deployments == 0                                              # a blocked deploy never ran
    assert row.inputs["attribution"] == "charge_origin" and row.inputs["facts"]["self_share_1h"] == 0.9
    assert replay(row.inputs, row.inputs_hash, row.verdict, row.reason_code).matches     # Recompute
    assert await valkey.xlen(gw.BRIEFS) == 1 and (await valkey.xpending(gw.STREAM, gw.GROUP))["pending"] == 0


async def test_redelivery_with_a_new_delivery_id_makes_no_second_row_or_callback(engine: AsyncEngine,
                                                                                 valkey: Redis) -> None:
    run_id, dep_id = ids()
    first = job("c" * 40, run_id, dep_id)
    posts: list[tuple[dict[str, Any], bool, str]] = []
    out1 = await gw.evaluate_job(context(engine, valkey, posts), first)
    out2 = await gw.evaluate_job(context(engine, valkey, posts), {**first, "delivery": str(uuid.uuid4())})
    assert (out1.status, out2.status) == ("decided", "duplicate") and len(posts) == 1


async def test_proven_rollback_is_admitted_free_during_a_page(engine: AsyncEngine, valkey: Redis) -> None:
    async with engine.begin() as c:
        await c.execute(text("""
            INSERT INTO deployments (service_id, sha, service_tree_sha, started_at, finished_at, status,
                                     is_change_failure)
            SELECT id, :sha, :tree, now() - INTERVAL '3 days', now() - INTERVAL '3 days', 'success', false
            FROM services WHERE name = 'orders-service'"""), {"sha": "0" * 40, "tree": TREE})
    run_id, dep_id = ids()
    posts: list[tuple[dict[str, Any], bool, str]] = []
    out = await gw.evaluate_job(context(engine, valkey, posts), job("b" * 40, run_id, dep_id))
    assert out.decision is not None and out.decision.reason_code == "ROLLBACK_LANE"
    assert out.decision.price_pct == 0.0 and out.decision.canary_profile == (100,) and posts[0][1] is True
    async with engine.connect() as c:
        ev = (await c.execute(text("SELECT inputs -> 'lane_evidence' FROM decisions WHERE id = :i"),
                              {"i": out.decision_id})).scalar_one()
        dep = (await c.execute(text("SELECT status, service_tree_sha FROM deployments "
                                    "WHERE decision_id = :i"),
                               {"i": out.decision_id})).one()
    assert ev["service_tree_sha"] == TREE and ev["matched_sha"] == "0" * 40
    assert (dep.status, dep.service_tree_sha) == ("in_progress", TREE)


async def test_override_is_consumed_once_under_two_concurrent_reruns(engine: AsyncEngine,
                                                                     valkey: Redis) -> None:
    sha = "d" * 32 + uuid.uuid4().hex[:8]
    run_id, dep_id = ids()
    posts: list[tuple[dict[str, Any], bool, str]] = []
    blocked = await gw.evaluate_job(context(engine, valkey, posts), job(sha, run_id, dep_id))
    assert blocked.decision is not None and blocked.decision.reason_code == "ACTIVE_PAGE"
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO overrides (decision_id, approver_id, approver, reason) "
                             "VALUES (:d, 501, 'alice', "
                             "'orders checkout is down; this is the fix, ship it')"),
                        {"d": blocked.decision_id})
    await valkey.flushdb()                                              # no cached verdict for the re-runs
    reruns = [job(sha, run_id, dep_id + 10), job(sha, run_id, dep_id + 11)]
    a, b = await asyncio.gather(*(gw.evaluate_job(context(engine, None, []), j) for j in reruns))
    codes = sorted(o.decision.reason_code for o in (a, b) if o.decision)
    assert codes == ["ACTIVE_PAGE", "OVERRIDE"]
    async with engine.connect() as c:
        consumed = (await c.execute(text("SELECT consumed_by_decision_id FROM overrides "
                                         "WHERE decision_id = :d"),
                                    {"d": blocked.decision_id})).scalar_one()
    winner = a if a.decision and a.decision.reason_code == "OVERRIDE" else b
    assert consumed == winner.decision_id


async def test_postgres_down_buffers_and_replays(engine: AsyncEngine, valkey: Redis) -> None:
    run_id, dep_id = ids()
    dead = create_async_engine("postgresql+asyncpg://bg:x@127.0.0.1:1/none")
    posts: list[tuple[dict[str, Any], bool, str]] = []
    out = await gw.evaluate_job(context(dead, valkey, posts), job("e" * 40, run_id, dep_id))
    await dead.dispose()
    assert out.status == "buffered" and len(posts) == 1
    assert await gw.replay_pending(context(engine, valkey, [])) == 1
    async with engine.connect() as c:
        n = (await c.execute(text("SELECT count(*) FROM decisions WHERE github_deployment_id = :d"),
                             {"d": dep_id})).scalar_one()
    assert n == 1 and await valkey.xlen(gw.PENDING) == 0
