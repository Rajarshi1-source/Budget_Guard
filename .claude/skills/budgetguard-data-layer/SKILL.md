---
name: budgetguard-data-layer
description: Design, migrate and query BudgetGuard's state — PostgreSQL 18 with TimescaleDB (sli_buckets hypertable of request-weighted 5-minute buckets, hourly and daily continuous aggregates, compression and retention, decisions, deployments, incidents, overrides, policy_versions), the snapshotter turning Prometheus increase() into idempotent bucket upserts under one leader, Alembic expand-migrate-contract migrations, DORA and MTTR SQL, and Valkey 9.1 for verdict cache, locks, delivery dedupe and streams with consumer groups and XAUTOCLAIM. Use for ANY schema, SQL, migration, snapshotter, hypertable, continuous aggregate, Valkey key or stream work in BudgetGuard, and for database or cache choice questions (TimescaleDB vs plain PostgreSQL, InfluxDB, MongoDB, Cassandra; Valkey vs Kafka, RabbitMQ), even if the user just says the DB. MANDATE - Prometheus stays the live source of truth; buckets are summed, never averaged; Valkey is never a source of truth.
---

# BudgetGuard — data layer (Rev 2 §5, §6)

Two stores, two jobs:

- **PostgreSQL 18 + TimescaleDB** is the system of record: SLI history the gate replays
  (budget, ETA, backtests), and every relational fact the audit trail needs (decisions,
  deployments, incidents, overrides). Strong consistency for decisions.
- **Valkey 9.1** (Redis 8 is a drop-in) is an accelerator and work queue. If it disappears the
  gate still decides correctly — slower — because PostgreSQL unique keys enforce idempotency.

Prometheus remains the **live** source of truth. Do not `remote_write` into TimescaleDB:
that needed Promscale, which Timescale discontinued (B-11). BudgetGuard's own snapshotter
writes 5-minute buckets instead.

Full DDL, every query and the Valkey key catalog: **`references/schema-and-queries.md`** — read
it before writing SQL or adding a key.

Pairs with: `budgetguard-slo-math` (sums these buckets), `budgetguard-admission-gate`
(writes decisions, reads history), `budgetguard-nextjs-dashboard` (reads the queries),
`budgetguard-mlops-evals` (replays buckets in the backtester).

---

## 1. The choice, honestly sized (interview-ready)

8 SLO series × 288 buckets/day = **2,304 rows/day**, ≈ 0.9 M rows in 13 months. Plain
PostgreSQL handles that. Rev 1 claimed 260 M rows per 90 days — about 100x too high (B-11).
TimescaleDB earns its place through **declarations, not volume**: continuous aggregates that
stay correct when late buckets are upserted, compression and retention as policies, and a path
to 500 services × 4 SLOs ≈ 576 k rows/day ≈ 210 M rows/year without hand-rolled partitioning.

| Option | Verdict | One-line reason |
| --- | --- | --- |
| PostgreSQL 18 + TimescaleDB | **Chosen** | Time-series lifecycle + joins + transactions in one database |
| Plain PostgreSQL 18 | Runner-up | Right answer at 4 services; say you'd start here at a startup |
| InfluxDB | Rejected | No relational model → second database for incidents/deploys |
| MongoDB | Rejected | `$lookup` joins; decision records lose FK integrity |
| Cassandra | Rejected | Built for write fan-out we don't have; no joins; 3+ nodes |
| Azure Cosmos DB | Rejected | Cloud lock-in, RU billing, partition-scoped transactions |
| Prometheus alone | Kept as live source | No relational data, not a system of record |

| Option | Verdict | One-line reason |
| --- | --- | --- |
| Valkey 9.1 | **Chosen** | Cache + locks + streams in one process; BSD licence |
| PostgreSQL `SKIP LOCKED` queue | Credible runner-up | Would work at ~50 events/day; Valkey wins only because cache and locks need it anyway |
| Kafka 4.x | Rejected | ZooKeeper-free KRaft now, but partitions/replay/schema registry solve problems we don't have |
| RabbitMQ | Rejected | Second broker for one queue |
| ZooKeeper | Not applicable | Coordination, not cache or queue; leader election uses a Lease or advisory lock |

Image: `timescale/timescaledb:<2.27.x>-pg18` (Community/TSL build). **Never the `-oss` tag** —
compression and continuous aggregates are TSL features. Use `timescale/timescaledb-ha` only if
you need Toolkit hyperfunctions. Note TimescaleDB 2.18+ renamed compression to *columnstore*
(`add_columnstore_policy`); the `compress` API used here still works as an alias — pick one
style and keep it consistent.

---

## 2. Schema changes vs Rev 2 (flag these to the user)

Rev 2's DDL is kept; four additions close gaps found while writing the gate and dashboard:

| Change | Why |
| --- | --- |
| `deployments.service_tree_sha TEXT` + index on `(service_id, service_tree_sha)` | Rollback lane must be *proven*: same git subtree as an earlier healthy deploy (see `budgetguard-admission-gate`) |
| Seed `policy_versions` on boot (`INSERT … ON CONFLICT DO NOTHING` with the current policy SHA) | `decisions.policy_version` is a FK; without a seed row the first decision fails |
| `incidents.alertname TEXT NOT NULL` and `incidents.slo TEXT` | MTTR "per incident type" needs a stable type; Alertmanager fingerprints alone don't say what fired |
| `sli_daily` continuous aggregate (hierarchical, on `sli_hourly`) | 13-month reports and the backtester read days, not 112 k buckets |
| `decisions.github_deployment_id BIGINT UNIQUE` (25 Oct) | The webhook delivery header is not signed, the deployment id is; a replayed body with a new delivery id must not create a second decision (`budgetguard-admission-gate/references/security-and-api.md` T-1) |
| `overrides.approver_id BIGINT` (25 Oct) | Approvers are authorized by numeric GitHub id; logins can be renamed and re-registered |

Verified 25 Oct 2026: `001`–`003` and every query in the reference ran against
`timescale/timescaledb:2.27.0-pg18` and `2.31.0-pg18` (Docker in WSL 2). Two query bugs were
found and fixed there: `round()` on `DOUBLE PRECISION` in the chargeback query (also in Rev 2
§5 query 4) and an ungrouped `o.objective` in the change-failure cost subquery.

---

## 3. The snapshotter — `workers/snapshotter.py`

Every 60 s it writes the **last completed, aligned** 5-minute bucket for every SLO, plus any
missing buckets in the last 6 hours (self-healing after downtime). Writes are upserts keyed by
`(slo_id, bucket_start)`, so reruns are harmless.

```python
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text

from ..config import settings
from ..db import engine
from ..slo_registry import load_slos     # slo_id -> SloQueries(total, bad, self_bad, type)

BUCKET = timedelta(minutes=5)
LEADER_LOCK = 724_001                    # pg advisory lock id: one writer across replicas

UPSERT = text("""
INSERT INTO sli_buckets (slo_id, bucket_start, total, bad, self_bad, integrity)
VALUES (:slo_id, :bucket_start, :total, :bad, :self_bad, :integrity)
ON CONFLICT (slo_id, bucket_start) DO UPDATE
SET total = EXCLUDED.total, bad = EXCLUDED.bad, self_bad = EXCLUDED.self_bad,
    integrity = EXCLUDED.integrity
""")


def aligned_end(now: datetime) -> datetime:
    floored = now.replace(second=0, microsecond=0)
    return floored - timedelta(minutes=floored.minute % 5)


async def scalar(client: httpx.AsyncClient, q: str, at: datetime) -> float | None:
    r = await client.get(f"{settings.prometheus_url}/api/v1/query",
                         params={"query": q, "time": at.timestamp()}, timeout=3.0)
    r.raise_for_status()
    result = r.json()["data"]["result"]
    return float(result[0]["value"][1]) if result else None


async def snapshot_bucket(client: httpx.AsyncClient, conn, slo, end: datetime) -> None:
    w = "[5m]"
    total = await scalar(client, f"sum(increase({slo.total}{w}))", end)
    if slo.type == "ratio":
        bad = await scalar(client, f"sum(increase({slo.bad}{w}))", end)
    else:                                   # latency: bad = total - good
        good = await scalar(client, f"sum(increase({slo.good}{w}))", end)
        bad = None if total is None or good is None else max(total - good, 0.0)
    self_bad = await scalar(client, f"sum(increase({slo.self_bad}{w}))", end) if slo.self_bad else 0.0
    integrity = "no_data" if total is None else ("ok" if total > 0 else "low_traffic")
    await conn.execute(UPSERT, {"slo_id": slo.id, "bucket_start": end - BUCKET,
                                "total": total or 0.0, "bad": bad or 0.0,
                                "self_bad": self_bad or 0.0, "integrity": integrity})


async def run() -> None:
    slos = load_slos()
    async with httpx.AsyncClient() as client, engine.connect() as lock_conn:
        while not (await lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                           {"k": LEADER_LOCK})).scalar():
            await asyncio.sleep(15)                     # standby replica
        while True:
            end = aligned_end(datetime.now(UTC))
            async with engine.begin() as conn:
                missing = (await conn.execute(text("""
                    SELECT s.id, g.t FROM slos s
                    CROSS JOIN generate_series(:from, :to, INTERVAL '5 minutes') AS g(t)
                    LEFT JOIN sli_buckets b ON b.slo_id = s.id AND b.bucket_start = g.t
                    WHERE s.active AND b.slo_id IS NULL"""),
                    {"from": end - timedelta(hours=6), "to": end - BUCKET})).all()
                for slo_id, start in missing:
                    await snapshot_bucket(client, conn, slos[slo_id], start + BUCKET)
            await asyncio.sleep(60)
```

**Built and verified in W4 (26 Oct 2026, `platform/budgetguard/workers/snapshotter.py`).** The
sketch above has five bugs the real file fixes; use the repo file, not the sketch:

| Sketch | Problem | Built version |
| --- | --- | --- |
| `aligned_end(now)` with no delay | at 12:05:01 the 12:00 bucket's last scrape has not landed; the short bucket is never revisited | `aligned_end(now - 45 s)`: three scrape intervals of settle |
| one instant query per bucket and series | a 30-day backfill is ~200k HTTP calls | one `query_range` per series and window, step 5m (`PrometheusClient.range_values`) |
| latency: `bad = None` when `good` is absent | every failed notification (no `outcome="delivered"` series) recorded as 0 bad | zero-fill exactly like the rules: no `bad` series = 0, no `good` series = all bad; bad clamped to [0, total] |
| rewrites any bucket in the window | a bucket older than Prometheus retention would be overwritten with `no_data` | fills only *missing* rows (`generate_series` left join), never rewrites |
| a Prometheus error propagates | the leader dies, or `no_data` rows get written for an observer outage | `PrometheusUnavailable` skips the tick; the next one backfills |

Also built: `--backfill-hours` and `--once` flags, `bg_snapshotter_leader` and
`bg_snapshotter_last_bucket_timestamp_seconds` gauges on `--metrics-port`, a `SELECT 1` on the lock
connection every tick (a dead lock connection ends the leader), and `load_slos` refusing to run when a
spec's `spec_hash` differs from its `slos` row (`bg sync-slos` runs in the migrate job).

Decisions baked in, worth defending:

- **Advisory lock instead of a Kubernetes Lease.** Rev 2 names a Lease; a PostgreSQL
  session-level advisory lock gives the same single-writer guarantee in docker-compose *and*
  Kubernetes with no RBAC. If the leader's connection dies the lock releases. Mention the Lease
  as equivalent; flag the substitution to the user.
- `increase()` extrapolates, so totals are floats and a bucket can differ from a raw count by
  a fraction — fine for SLOs; never round to int.
- An unscraped service yields `total = None` → `integrity = 'no_data'` stored explicitly.
  The 30-day sum ignores it numerically, and the gate's integrity check catches it live.
- Prometheus holds 35 days, so a snapshotter outage shorter than that self-heals; widen the
  6-hour backfill window after long outages.

---

## 4. Valkey usage (all keys live in the catalog in the reference)

```python
# queue.py — idempotent enqueue: delivery-ID dedupe + stream append in one round trip
from redis.asyncio import Redis

ENQUEUE_ONCE = """
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', 604800) then
  redis.call('XADD', KEYS[2], 'MAXLEN', '~', 10000, '*', 'job', ARGV[1])
  return 1
end
return 0
"""

async def enqueue_once(r: Redis, stream: str, key: str, job: dict) -> bool:
    import json
    return bool(await r.eval(ENQUEUE_ONCE, 2, f"bg:delivery:{key}", stream, json.dumps(job)))
```

Consumers use `XREADGROUP GROUP gate-workers <consumer> COUNT 10 BLOCK 5000 STREAMS
bg:gate-evaluations >`, `XACK` after the decision commit and callback, and run
`XAUTOCLAIM … 60000` on a timer to adopt jobs from a crashed worker. Create groups at
startup with `XGROUP CREATE … $ MKSTREAM` and ignore `BUSYGROUP`.

When Valkey is down: the webhook handler evaluates inline and relies on
`UNIQUE (decisions.github_delivery)` with `ON CONFLICT DO NOTHING`.

---

## 5. Migrations (expand → migrate → contract)

Alembic, one release apart: add nullable column → backfill job → make `NOT NULL` / drop the old
column in the *next* release. Hypertable DDL (`create_hypertable`, policies, continuous
aggregates) goes in raw SQL migrations; continuous aggregate creation cannot run in a
transaction block (Alembic `context.configure(transaction_per_migration=True)` plus
`with op.get_context().autocommit_block():`).

How the repo runs the SQL files (W4, verified on 2.31.0-pg18):

- Alembic is **synchronous on psycopg 3** (`env.py` swaps `+asyncpg` for `+psycopg`): asyncpg
  prepares every statement, so it cannot run a multi-statement file.
- Files go through `op.get_bind().connection.driver_connection.cursor().execute(sql)` with **no
  parameters**. SQLAlchemy `text()` treats `:word` as a bind, and `exec_driver_sql` passes an empty
  tuple, so psycopg parsed `%H` in a comment as a placeholder.
- `002` is split per statement (also when a line ends `;  -- comment`) and run in an autocommit
  block: a multi-statement string is one implicit transaction, and cagg creation fails inside it.
- Role passwords: `SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', CAST(:r AS text), CAST(:p AS text))`
  then execute the result. The casts avoid `IndeterminateDatatype`; only roles whose
  `BG_DB_<ROLE>_PASSWORD` is set get LOGIN.
- Better Auth's tables are migration `006`, run under `SET LOCAL ROLE bg_auth; SET LOCAL search_path
  = auth` and followed by `RESET ROLE; RESET search_path`, because Alembic records the revision in
  `public.alembic_version` inside the same transaction.

## 6. Pitfalls

| Pitfall | Consequence | Rule |
| --- | --- | --- |
| Averaging per-bucket availability | quiet night buckets weigh like peak ones | `sum(bad)/sum(total)` only |
| `TIMESTAMP` without time zone | DST/offset bugs in MTTR (B-14) | `TIMESTAMPTZ` everywhere |
| MTTR as `avg()` | one 9-hour incident hides ten 5-minute ones | `percentile_cont(0.5)` and `0.9` |
| `-oss` image | continuous aggregate DDL fails | TSL image |
| Reading the cagg's last hour as final | real-time aggregation not enabled by default since 2.13 | the repo sets `materialized_only = false` on both caggs (migration `007`) |
| Backfilling more than the policy's `start_offset` (3 days) | old buckets sit below the cagg watermark and never reach `sli_hourly`, real-time or not | `CALL refresh_continuous_aggregate(...)` as the **owner** (`bg_app` gets "must be owner"): `make backfill` does it |
| `:now - INTERVAL '30 days'` with asyncpg | asyncpg infers `:now` as an interval: `operator does not exist: timestamp with time zone >= interval` | compute the bound in Python, or `CAST(:now AS timestamptz)` |
| Change-failure blames deploys for dependency outages | inflated CFR (B-15) | only self-inflicted burn (`self_bad`) marks a change failure |

## 7. Interview soundbites

- "Two thousand rows a day — plain Postgres would be fine, and I'd say so. I chose
  TimescaleDB for continuous aggregates that stay correct under late upserts and for
  retention as a policy, not for volume."
- "Valkey is an accelerator, never the truth. Kill it and the gate still answers, because
  idempotency lives in a Postgres unique key."
- "MTTR is a median and a p90 per incident type. A mean lets one long night hide everything."
