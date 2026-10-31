"""Gate worker (W5.6): the seven-step contract of budgetguard-admission-gate §5.

    python -m budgetguard.workers.gate_worker

1. read bg:gate-evaluations (group gate-workers), XAUTOCLAIM jobs idle > 60 s; validate_job()
   first, so a forged job is dead-lettered and never mints a token; skip a job whose
   deployment_id already has a decision (re-sending its callback if that never went out)
2. single-flight per service: SET bg:lock:eval:{service} NX PX 15000
3. verdict cache bg:verdict:{service}:{gen}:{sha} (30 s): a re-run reuses the first evaluation's
   facts, so the history the backtester replays is the facts the first decision saw
4. gather concurrently (asyncio.TaskGroup): live signals, history, rollback subtree SHA
5. one transaction: override proof (row lock), rollback proof, evaluate(), ETA if blocked,
   insert the decision, consume the override, insert the deployment row
6. callback with retries; the decision is already committed, so GitHub is never told something
   the audit trail does not say
7. XADD bg:briefs, XACK, release the lock

PostgreSQL down: the record goes to bg:records-pending (Valkey, AOF) before the callback and is
replayed into decisions on recovery. Valkey down: the webhook handler evaluates inline
(evaluate_job with valkey=None); PostgreSQL's UNIQUE keys dedupe.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import socket
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .. import redact
from ..briefs import Eta, template_comment
from ..changes import Change, PullRequest, extract
from ..config import Settings, get_settings
from ..db import DB_DOWN
from ..eta import BUCKET, Bucket, unfreeze_eta
from ..gate import Decision, GateInput, Lane, Verdict, evaluate
from ..gather import History, Signals, gather_signals, history
from ..integrations.github_app import AppMisconfigured, JobRejected, post_review, validate_job
from ..integrations.prometheus import PrometheusClient
from ..integrity import Integrity
from ..lanes import (
    LaneProof,
    consume_override,
    incident_fix_proof,
    override_proof,
    rollback_proof,
    service_tree_sha,
)
from ..policy import Policy
from ..pricing import ChangeFeatures
from ..records import DeployFacts, build_inputs, to_gate_input
from ..rulegen.spec import ServiceLevel, load_dir

log = logging.getLogger("budgetguard.gate_worker")

STREAM, GROUP = "bg:gate-evaluations", "gate-workers"
DEAD, BRIEFS, PENDING = "bg:gate-dead", "bg:briefs", "bg:records-pending"
LOCK_MS, CACHE_S, CLAIM_IDLE_MS = 15_000, 30, 60_000
RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""


@dataclass
class Context:
    settings: Settings
    policy: Policy
    specs: dict[str, ServiceLevel]
    prom: PrometheusClient
    engine: AsyncEngine | None
    valkey: Any | None                       # redis.asyncio.Redis; None = Valkey down (inline mode)
    http: httpx.AsyncClient
    github: Callable[[int], Any] = field(default=lambda i: None)   # installation id -> TreeReader
    post: Callable[..., Awaitable[None]] = post_review
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    callback_attempts: int = 8               # ~4 min of backoff inline; XAUTOCLAIM covers longer outages


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str                              # decided | duplicate | resent | rejected | buffered
    decision_id: int | None = None
    decision: Decision | None = None


# ---------------------------------------------------------------- SQL
EXISTING = text("""
SELECT id, verdict, callback_state, inputs -> 'comment' AS comment
FROM decisions WHERE github_deployment_id = :dep OR github_delivery = :delivery
ORDER BY id LIMIT 1
""")

INSERT_DECISION = text("""
INSERT INTO decisions (service_id, github_delivery, github_deployment_id, github_run_id, sha, policy_version,
                       inputs_hash, inputs, verdict, reason_code, price_pct, affordable_pct, canary_profile,
                       brief_source)
SELECT s.id, :delivery, :deployment_id, :run_id, :sha, :policy_version, :inputs_hash, CAST(:inputs AS jsonb),
       :verdict, :reason_code, :price_pct, :affordable_pct, CAST(:canary_profile AS smallint[]), 'template'
FROM services s WHERE s.name = :service
ON CONFLICT DO NOTHING
RETURNING id
""")

INSERT_DEPLOYMENT = text("""
INSERT INTO deployments (service_id, decision_id, sha, service_tree_sha, lines_changed, touches_migration,
                         config_only, dependency_bump, started_at, status, pr_number, first_commit_at)
SELECT s.id, :decision_id, :sha, :tree_sha, :lines, :migration, :config_only, :dependency, :at, 'in_progress',
       :pr_number, :first_commit_at
FROM services s WHERE s.name = :service
""")

ETA_BUCKETS = """
SELECT b.bucket_start, b.{col} AS bad, b.total
FROM sli_buckets b JOIN slos o ON o.id = b.slo_id JOIN services s ON s.id = o.service_id
WHERE s.name = :service AND o.name = :slo AND o.active AND b.bucket_start >= :start AND b.bucket_start < :end
"""


# ---------------------------------------------------------------- pieces
def decision_url(settings: Settings, decision_id: int | None) -> str:
    return f"{settings.public_url}/decisions/{decision_id}" if decision_id else settings.public_url


def affordable_threshold(d: Decision, policy: Policy) -> float | None:
    """The budget at which this deploy's price becomes affordable: k * (remaining - reserve) >= price.
    Only an OVER_BUDGET block has one; an ACTIVE_PAGE block clears when the page resolves, and an
    unknown SLI clears when the signal returns, so a budget projection would mislead."""
    if d.reason_code != "OVER_BUDGET" or d.price_pct is None:
        return None
    return policy.reserve_pct + d.price_pct / policy.spend_fraction


async def eta_for(conn: AsyncConnection, sl: ServiceLevel, slo_name: str, policy: Policy,
                  now: datetime, threshold_pct: float) -> Eta:
    """D-5 over the constraining SLO's last 30 days; self_bad under charge_origin when the SLI has it."""
    slo = next(s for s in sl.spec.slos if s.name == slo_name)
    col = "self_bad" if policy.attribution == "charge_origin" and slo.sli.self_bad else "bad"
    end = now.replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % 5)
    start = end - timedelta(days=30)
    rows = (await conn.execute(text(ETA_BUCKETS.format(col=col)),
                               {"service": sl.metadata.service, "slo": slo_name,
                                "start": start, "end": end})).all()
    have = {r.bucket_start: (float(r.bad), float(r.total)) for r in rows}
    n = int((end - start) / BUCKET)
    window = [Bucket(t, *have.get(t, (0.0, 0.0))) for t in (start + i * BUCKET for i in range(n))]
    allowed = 1 - slo.objective
    return Eta(unfreeze_eta(window, slo.objective, threshold_pct),
               unfreeze_eta(window, slo.objective, threshold_pct, future_error_ratio=allowed / 2))


def make_facts(job: dict[str, Any], sl: ServiceLevel, sig: Signals, hist: History, lane: Lane | None,
               now: datetime, features: ChangeFeatures) -> DeployFacts:
    return DeployFacts(
        case_id=job["delivery"], service=sl.metadata.service, at=now, integrity=sig.integrity,
        remaining_all_pct=sig.remaining_all_pct, remaining_self_pct=sig.remaining_self_pct,
        page_firing=sig.page_firing, self_share_1h=sig.self_share_1h, lane=lane,
        features=features,
        prior_deploys=hist.deploys, prior_failures=hist.failures,
        median_failure_cost_pct=hist.median_failure_cost_pct, failure_mode=sl.spec.failure_mode)


async def _callback(ctx: Context, job: dict[str, Any], approved: bool, comment: str) -> bool:
    delay = 1.0
    for attempt in range(ctx.callback_attempts):
        try:
            await ctx.post(ctx.http, job, approved, comment)
            return True
        except JobRejected:
            raise
        except AppMisconfigured as e:              # permanent: record it, do not retry forever
            log.error("callback for %s impossible: %s", job["delivery"], e)
            return False
        except (httpx.HTTPError, OSError) as e:
            log.warning("callback attempt %d for %s failed: %s", attempt + 1, job["delivery"],
                        type(e).__name__)
            await asyncio.sleep(delay + random.uniform(0, delay / 2))
            delay = min(delay * 2, 60.0)
    return False


async def _mark_callback(ctx: Context, decision_id: int | None, sent: bool) -> None:
    if ctx.engine is None or decision_id is None:
        return
    try:
        async with ctx.engine.begin() as c:
            await c.execute(text("UPDATE decisions SET callback_state = :s WHERE id = :i"),
                            {"s": "sent" if sent else "failed", "i": decision_id})
    except DB_DOWN:
        log.warning("could not record callback state for decision %s", decision_id)


UNKNOWN_CHANGE = Change(ChangeFeatures.unknown(), None,
                        note="PR data unavailable: priced at the largest size")


async def _gather(ctx: Context, job: dict[str, Any], sl: ServiceLevel,
                  now: datetime) -> tuple[Signals, History, str | None, Change]:
    async def tree() -> str | None:
        reader = ctx.github(job["installation_id"])
        if reader is None:
            return None
        try:
            return await service_tree_sha(reader, job["repo"], job["sha"], sl.metadata.service)
        except (httpx.HTTPError, KeyError, AppMisconfigured) as e:
            log.warning("subtree lookup failed (%s): rollback lane unprovable", type(e).__name__)
            return None

    async def hist() -> History:
        if ctx.engine is None:
            return History(0, 0, None)
        try:
            return await history(ctx.engine, sl.metadata.service, now)
        except DB_DOWN:
            return History(0, 0, None)

    async def change() -> Change:
        reader = ctx.github(job["installation_id"])
        if reader is None:
            return UNKNOWN_CHANGE
        try:
            return await extract(reader, job["repo"], job["sha"], sl.metadata.service)
        except (httpx.HTTPError, KeyError, TypeError, AttributeError, ValueError, AppMisconfigured) as e:
            log.warning("change extraction failed (%s): priced at size_max", type(e).__name__)
            return UNKNOWN_CHANGE

    async with asyncio.TaskGroup() as tg:
        s = tg.create_task(gather_signals(ctx.prom, ctx.engine, sl, now))
        h = tg.create_task(hist())
        t = tg.create_task(tree())
        c = tg.create_task(change())
    return s.result(), h.result(), t.result(), c.result()


def _eta_json(eta: Eta | None, now: datetime) -> dict[str, Any] | None:
    """The Freeze Brief explains the ETA from the record (brief_view), so store it with the decision."""
    if eta is None or eta.if_clean is None:
        return None
    return {"eta_utc": eta.if_clean.strftime("%Y-%m-%dT%H:%MZ"),
            "eta_in_hours": round((eta.if_clean - now).total_seconds() / 3600, 1),
            "at_half_budget_utc": (eta.at_half_budget.strftime("%Y-%m-%dT%H:%MZ")
                                   if eta.at_half_budget else None)}


def _first_commit(c: Change) -> datetime | None:
    if c.pr is None or not c.pr.first_commit_at:
        return None
    return datetime.fromisoformat(c.pr.first_commit_at.replace("Z", "+00:00"))


def _change_json(c: Change) -> dict[str, Any]:
    return {"features": asdict(c.features), "note": c.note, "service_files": c.service_files,
            "pr": asdict(c.pr) if c.pr else None}


def _change_from_json(d: dict[str, Any]) -> Change:
    return Change(ChangeFeatures(**d["features"]), PullRequest(**d["pr"]) if d["pr"] else None,
                  service_files=d["service_files"], note=d["note"])


async def _cached(ctx: Context, service: str, sha: str) -> tuple[str, dict[str, Any] | None]:
    if ctx.valkey is None:
        return "", None
    gen = await ctx.valkey.get(f"bg:verdict-gen:{service}") or "0"
    key = f"bg:verdict:{service}:{gen}:{sha}"
    raw = await ctx.valkey.get(key)
    return key, (json.loads(raw) if raw else None)


# ---------------------------------------------------------------- the contract
async def evaluate_job(ctx: Context, job: dict[str, Any]) -> Outcome:
    """Steps 1 and 3-6 for one job. Raises JobRejected for a job that must never mint a token."""
    validate_job(job)
    sl = ctx.specs.get(job["service"])
    if sl is None:
        raise JobRejected(f"no SLO spec for {job['service']}")
    now = ctx.clock()

    if ctx.engine is not None:
        try:
            async with ctx.engine.connect() as c:
                prev = (await c.execute(EXISTING, {"dep": job.get("deployment_id"),
                                                   "delivery": job["delivery"]})).first()
        except DB_DOWN:
            prev = None
        if prev is not None:                     # redelivery with a new delivery id, or a crashed worker
            if prev.callback_state == "sent":
                return Outcome("duplicate", prev.id)
            approved = prev.verdict != "block"
            sent = await _callback(ctx, job, approved, prev.comment or decision_url(ctx.settings, prev.id))
            await _mark_callback(ctx, prev.id, sent)
            return Outcome("resent", prev.id)

    cache_key, cached = await _cached(ctx, sl.metadata.service, job["sha"])
    if cached is not None:
        sig = Signals(**{**cached["signals"], "integrity": Integrity(cached["signals"]["integrity"])})
        hist, tree_sha = History(**cached["history"]), cached["tree_sha"]
        change = _change_from_json(cached["change"])
        now = datetime.fromisoformat(cached["at"])
    else:
        sig, hist, tree_sha, change = await _gather(ctx, job, sl, now)

    github = {k: job.get(k) for k in ("repo", "installation_id", "environment", "callback_url", "run_id")}
    try:
        if ctx.engine is None:
            raise OSError("no database configured")
        async with ctx.engine.begin() as conn:
            override = await override_proof(conn, sl.metadata.service, job["sha"])
            rollback = await rollback_proof(conn, sl.metadata.service, tree_sha)
            fix = await incident_fix_proof(conn, ctx.prom, sl.metadata.service, change.pr)
            proof = next((p for p in (rollback, override, fix) if p.lane is not None), LaneProof(None))
            facts, gi, d = _decide(ctx, job, sl, sig, hist, proof, now, change.features)
            threshold = affordable_threshold(d, ctx.policy)
            eta = (await eta_for(conn, sl, sig.constraining_slo, ctx.policy, now, threshold)
                   if threshold is not None else None)
            evidence = {**rollback.evidence, **override.evidence, **fix.evidence}
            inputs = build_inputs(facts, gi, ctx.policy, evidence, github) | {"change": _change_json(change),
                                                                              "eta": _eta_json(eta, now)}
            inputs["comment"] = ""                    # filled after the id is known (decision URL)
            row = (await conn.execute(INSERT_DECISION, _decision_params(job, gi, d, inputs))).first()
            if row is None:                           # another worker won the race on a unique key
                return Outcome("duplicate")
            decision_id = int(row.id)
            comment = template_comment(sl.metadata.service, d, decision_url(ctx.settings, decision_id), eta,
                                       gi.remaining_pct, note=change.note)
            await conn.execute(text("UPDATE decisions SET inputs = jsonb_set(inputs, '{comment}', to_jsonb("
                                    "CAST(:c AS text))) WHERE id = :i"), {"c": comment, "i": decision_id})
            if proof.override_id is not None and proof is override:
                await consume_override(conn, proof.override_id, decision_id)
            if d.verdict is not Verdict.BLOCK:
                f = change.features if change.pr else ChangeFeatures(0, False, False, False)
                await conn.execute(INSERT_DEPLOYMENT, {
                    "decision_id": decision_id, "sha": job["sha"], "tree_sha": tree_sha,
                    "service": sl.metadata.service, "lines": f.lines_changed,
                    "migration": f.touches_migration,
                    "config_only": f.config_only, "dependency": f.dependency_bump, "at": now,
                    "pr_number": change.pr.number if change.pr else None,
                    "first_commit_at": _first_commit(change)})
        status = "decided"
    except DB_DOWN:
        # PostgreSQL unavailable: no lane proof can be checked; buffer the record durably first.
        facts, gi, d = _decide(ctx, job, sl, sig, hist, LaneProof(None), now, change.features)
        if ctx.valkey is None:
            raise                                    # neither store: leave the run waiting (fail-closed)
        decision_id = None
        comment = template_comment(sl.metadata.service, d, decision_url(ctx.settings, None), None,
                                   gi.remaining_pct, note=change.note)
        inputs = build_inputs(facts, gi, ctx.policy, {}, github) | {"comment": comment,
                                                                    "change": _change_json(change)}
        await ctx.valkey.xadd(PENDING, {"record": json.dumps(_decision_params(job, gi, d, inputs))},
                              maxlen=10_000, approximate=True)
        status = "buffered"

    if cached is None and cache_key and ctx.valkey is not None and proof_lane_cacheable(d):
        await ctx.valkey.set(cache_key, json.dumps({
            "signals": _signals_json(sig),
            "history": {"deploys": hist.deploys, "failures": hist.failures,
                        "median_failure_cost_pct": hist.median_failure_cost_pct},
            "tree_sha": tree_sha, "change": _change_json(change), "at": now.isoformat()}), ex=CACHE_S)

    sent = await _callback(ctx, job, d.verdict is not Verdict.BLOCK, comment)
    await _mark_callback(ctx, decision_id, sent)
    if ctx.valkey is not None and decision_id is not None:
        await ctx.valkey.xadd(BRIEFS, {"decision_id": str(decision_id)}, maxlen=10_000, approximate=True)
    return Outcome(status, decision_id, d)


def proof_lane_cacheable(d: Decision) -> bool:
    return d.reason_code != "OVERRIDE"               # an override is single use: never replay its facts


def _signals_json(s: Signals) -> dict[str, Any]:
    return {"integrity": str(s.integrity), "remaining_all_pct": s.remaining_all_pct,
            "remaining_self_pct": s.remaining_self_pct, "page_firing": s.page_firing,
            "self_share_1h": s.self_share_1h, "constraining_slo": s.constraining_slo}


def _decide(ctx: Context, job: dict[str, Any], sl: ServiceLevel, sig: Signals, hist: History,
            proof: LaneProof, now: datetime,
            features: ChangeFeatures) -> tuple[DeployFacts, GateInput, Decision]:
    facts = make_facts(job, sl, sig, hist, proof.lane, now, features)
    gi = to_gate_input(facts, ctx.policy)
    return facts, gi, evaluate(gi)


def _decision_params(job: dict[str, Any], gi: GateInput, d: Decision,
                     inputs: dict[str, Any]) -> dict[str, Any]:
    return {"service": job["service"], "delivery": job["delivery"], "deployment_id": job.get("deployment_id"),
            "run_id": job.get("run_id"), "sha": job["sha"], "policy_version": gi.policy_version,
            "inputs_hash": gi.digest(), "inputs": json.dumps(inputs), "verdict": str(d.verdict),
            "reason_code": d.reason_code, "price_pct": d.price_pct, "affordable_pct": d.affordable_pct,
            "canary_profile": list(d.canary_profile)}


async def replay_pending(ctx: Context) -> int:
    """Drain bg:records-pending into decisions once PostgreSQL is back (ON CONFLICT DO NOTHING)."""
    if ctx.valkey is None or ctx.engine is None:
        return 0
    n = 0
    for entry_id, fields in await ctx.valkey.xrange(PENDING, count=100):
        try:
            async with ctx.engine.begin() as c:
                await c.execute(INSERT_DECISION, json.loads(fields["record"]))
        except DB_DOWN:
            return n
        await ctx.valkey.xdel(PENDING, entry_id)
        n += 1
    return n


# ---------------------------------------------------------------- the loop
async def handle(ctx: Context, entry_id: str, fields: dict[str, str]) -> None:
    assert ctx.valkey is not None
    try:
        job = json.loads(fields["job"])
    except (KeyError, ValueError):
        await _dead(ctx, entry_id, fields, "unparseable")
        return
    try:
        validate_job(job)
    except JobRejected as e:
        await _dead(ctx, entry_id, fields, str(e))
        return
    token, lock = socket.gethostname() + entry_id, f"bg:lock:eval:{job['service']}"
    while not await ctx.valkey.set(lock, token, nx=True, px=LOCK_MS):
        await asyncio.sleep(0.5)                     # single-flight; the lock expires after 15 s at most
    try:
        out = await evaluate_job(ctx, job)
        log.info("%s %s %s -> %s %s", job["service"], job["sha"][:7], job["delivery"], out.status,
                 out.decision.reason_code if out.decision else "")
    except JobRejected as e:
        await _dead(ctx, entry_id, fields, str(e))
        return
    finally:
        await ctx.valkey.eval(RELEASE, 1, lock, token)
    await ctx.valkey.xack(STREAM, GROUP, entry_id)


async def _dead(ctx: Context, entry_id: str, fields: dict[str, str], why: str) -> None:
    assert ctx.valkey is not None
    log.warning("dead-lettered %s: %s", entry_id, why)
    await ctx.valkey.xadd(DEAD, {**fields, "reason": why[:200]}, maxlen=10_000, approximate=True)
    await ctx.valkey.xack(STREAM, GROUP, entry_id)


async def run(consumer: str) -> None:
    from redis.asyncio import Redis
    from redis.exceptions import ResponseError

    from ..db import make_engine
    from ..integrations.github_app import GitHubApi as Api

    settings = get_settings()
    # socket timeout above XREADGROUP's 5 s block, or every idle read ends in a TimeoutError
    valkey = Redis.from_url(settings.valkey_url, decode_responses=True, socket_timeout=15,
                            socket_connect_timeout=5, health_check_interval=30)
    try:
        await valkey.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    engine = make_engine(pool_size=5)
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as http:
        ctx = Context(settings=settings, policy=load_policy(settings), specs=load_specs(settings),
                      prom=PrometheusClient(settings.prometheus_url, http, settings.prometheus_timeout_s),
                      engine=engine, valkey=valkey, http=http, github=lambda i: Api(http, i))
        await ensure_policy(engine, ctx.policy)
        sem, tasks = asyncio.Semaphore(8), set()

        async def guarded(entry_id: str, fields: dict[str, str]) -> None:
            async with sem:
                try:
                    await handle(ctx, entry_id, fields)
                except Exception:                    # noqa: BLE001 - one bad job must not stop the worker
                    log.exception("job %s failed; it stays pending for XAUTOCLAIM", entry_id)

        log.info("gate worker %s: policy %s, %d services", consumer, ctx.policy.version, len(ctx.specs))
        while True:
            auto = await valkey.xautoclaim(STREAM, GROUP, consumer, CLAIM_IDLE_MS, "0-0", count=10)
            claimed: list[Any] = list(auto[1])
            fresh: Any = await valkey.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=10, block=5000)
            batch = claimed + [e for _, entries in (fresh or []) for e in entries]   # RESP2 shape
            for entry_id, fields in batch:
                t = asyncio.create_task(guarded(entry_id, fields))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
            await replay_pending(ctx)


def load_policy(settings: Settings) -> Policy:
    from pathlib import Path
    return Policy.from_yaml(Path(settings.policy_file), settings.policy_version)


def load_specs(settings: Settings) -> dict[str, ServiceLevel]:
    return {sl.metadata.service: sl for sl in load_dir(settings.slo_dir)}


async def ensure_policy(engine: AsyncEngine, policy: Policy) -> None:
    """decisions.policy_version is a FK: the running version must exist before the first decision."""
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO policy_versions (version) VALUES (:v) ON CONFLICT DO NOTHING"),
                        {"v": policy.version})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gate_worker")
    ap.add_argument("--consumer", default=socket.gethostname())
    a = ap.parse_args(argv)
    redact.install()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(a.consumer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
