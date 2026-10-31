"""Deploy recorder (W6.3): deployments rows, lead time and the change-failure timer.

    python -m budgetguard.workers.deploy_events

Reads bg:deploy-events (group deploy-recorders), written by the webhook:
  deployment_status  the admitted decision's deployment row -> success / failure with finished_at.
                     A failed rollout (canary aborted, job failed) is a change failure.
  pull_request       merged PRs fill pr_number and first_commit_at (lead time) when the decision
                     could not read them itself.

Every 5 minutes, deploys whose 60-minute window has closed are settled (query 4.9, made
self-inflicted for D-3 / B-15): a change failure needs a page incident on the service in the window
AND at least self_page_share of the window's bad events to be the service's own. The window's
self-inflicted cost (burn x 1 h / 720 h, slo-math budget_spent) is stored either way; the pricing
median reads it from failures only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .. import redact
from ..config import get_settings
from ..db import DB_DOWN

log = logging.getLogger("budgetguard.deploy_events")

STREAM, GROUP = "bg:deploy-events", "deploy-recorders"
SETTLE_EVERY_S = 300
TERMINAL = {"success": "success", "failure": "failure", "error": "failure", "inactive": None}

DEPLOYMENT_STATUS = text("""
UPDATE deployments d
SET status = :status, finished_at = :at,
    is_change_failure = CASE WHEN :status = 'failure' THEN true ELSE d.is_change_failure END
FROM decisions x
WHERE d.decision_id = x.id AND x.github_deployment_id = :deployment_id AND d.status = 'in_progress'
RETURNING d.id
""")

PULL_REQUEST = text("""
UPDATE deployments
SET pr_number = COALESCE(pr_number, :number), first_commit_at = COALESCE(first_commit_at, :created_at)
WHERE sha = :sha
""")

# Query 4.9, self-inflicted: half-open window [finished_at, finished_at + 60 min).
SETTLE = text("""
WITH w AS (
    SELECT d.id,
           sum(b.total) AS total, sum(b.bad) AS bad, sum(b.self_bad) AS self_bad,
           max(o.objective) AS objective,
           EXISTS (SELECT 1 FROM incidents i
                   WHERE i.service_id = d.service_id AND i.severity = 'page'
                     AND i.started_at >= d.finished_at
                     AND i.started_at < d.finished_at + INTERVAL '60 minutes') AS paged
    FROM deployments d
    LEFT JOIN slos o ON o.service_id = d.service_id AND o.name = 'availability'
    LEFT JOIN sli_buckets b ON b.slo_id = o.id
         AND b.bucket_start >= d.finished_at AND b.bucket_start < d.finished_at + INTERVAL '60 minutes'
    WHERE d.is_change_failure IS NULL AND d.status = 'success'
      AND d.finished_at + INTERVAL '60 minutes' <= :now
    GROUP BY d.id
)
UPDATE deployments d
SET is_change_failure = w.paged AND COALESCE(w.self_bad / NULLIF(w.bad, 0), 0) >= :self_share,
    change_failure_budget_pct = 100 * (w.self_bad / NULLIF(w.total, 0)) / (1 - w.objective) / 720
FROM w WHERE d.id = w.id
RETURNING d.id, d.is_change_failure
""")


def _ts(value: str | None) -> datetime | None:
    return None if not value else datetime.fromisoformat(value.replace("Z", "+00:00"))


async def record(conn: AsyncConnection, rec: dict[str, Any]) -> str:
    """One event -> at most one UPDATE. Returns what happened, for the log."""
    if rec.get("event") == "deployment_status":
        status = TERMINAL.get(rec.get("state", ""))
        if status is None:
            return f"ignored state {rec.get('state')}"
        rows = (await conn.execute(DEPLOYMENT_STATUS, {"status": status, "at": _ts(rec.get("at")),
                                                       "deployment_id": int(rec["deployment_id"])})).all()
        return f"{status} ({len(rows)} row)" if rows else "no in-progress deployment for it"
    if rec.get("event") == "pull_request" and rec.get("merge_commit_sha"):
        n = (await conn.execute(PULL_REQUEST, {"number": int(rec["number"]), "sha": rec["merge_commit_sha"],
                                               "created_at": _ts(rec.get("created_at"))})).rowcount
        return f"PR #{rec['number']} -> {n} deployment(s)"
    return "ignored"


async def settle(engine: AsyncEngine, now: datetime, self_share: float) -> list[tuple[int, bool]]:
    async with engine.begin() as c:
        rows = (await c.execute(SETTLE, {"now": now, "self_share": self_share})).all()
    return [(int(r.id), bool(r.is_change_failure)) for r in rows]


async def run(consumer: str) -> None:
    from redis.asyncio import Redis
    from redis.exceptions import ResponseError

    from ..db import make_engine
    from .gate_worker import load_policy

    settings = get_settings()
    policy = load_policy(settings)
    valkey = Redis.from_url(settings.valkey_url, decode_responses=True, socket_timeout=15,
                            socket_connect_timeout=5, health_check_interval=30)
    try:
        await valkey.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    engine = make_engine(pool_size=2)
    last_settle = 0.0
    log.info("deploy recorder %s", consumer)
    while True:
        auto = await valkey.xautoclaim(STREAM, GROUP, consumer, 60_000, "0-0", count=20)
        fresh: Any = await valkey.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=20, block=5000)
        for entry_id, fields in list(auto[1]) + [e for _, es in (fresh or []) for e in es]:
            try:
                async with engine.begin() as c:
                    log.info("%s: %s", entry_id, await record(c, json.loads(fields["job"])))
            except DB_DOWN:
                break                                    # stays pending; XAUTOCLAIM retries it
            except (KeyError, ValueError) as e:
                log.warning("dropping malformed event %s: %s", entry_id, e)
            await valkey.xack(STREAM, GROUP, entry_id)
        loop = asyncio.get_running_loop()
        if loop.time() - last_settle >= SETTLE_EVERY_S:
            try:
                done = await settle(engine, datetime.now(UTC), policy.self_page_share)
                if done:
                    log.info("settled %d deploys, %d change failures", len(done), sum(f for _, f in done))
                last_settle = loop.time()
            except DB_DOWN:
                log.warning("settle skipped: database unavailable")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deploy_events")
    ap.add_argument("--consumer", default=socket.gethostname())
    a = ap.parse_args(argv)
    redact.install()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(a.consumer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
