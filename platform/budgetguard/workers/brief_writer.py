"""Freeze Brief writer (W7.4, W7.6).

    python -m budgetguard.workers.brief_writer

Reads bg:briefs (group brief-writers): one entry per committed decision. For each, builds the brief
view from the decision record only, writes the brief (LLM with runtime grounding, template on any
failure), stores it on the decision (inputs.brief, brief_source) and delivers it: #bg-briefs on
Slack and a comment on the merged PR, each only when configured. Off the critical path: GitHub
was answered with the template comment before this runs.

Every 5 minutes it also checks services whose latest decision was a block; once the gate's own
numbers allow deploys again (no self-inflicted page, budget above the reserve) it posts the
"budget recovered" note, once per blocked decision.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .. import redact
from ..briefs import BriefWriter, LlmConfig, ValkeySpendLedger, brief_view, load_prompt, recovered_brief
from ..config import Settings, get_settings
from ..db import DB_DOWN
from ..grounding import check
from ..integrations import slack
from ..integrations.github_app import installation_token
from ..integrations.llm import build_model
from ..policy import Policy
from ..rulegen.spec import ServiceLevel

log = logging.getLogger("budgetguard.brief_writer")

STREAM, GROUP = "bg:briefs", "brief-writers"
RECOVERY_EVERY_S = 300
RECOVERY_SKIP: tuple[type[BaseException], ...] = (*DB_DOWN, httpx.HTTPError)

DECISION = text("""
SELECT d.id, s.name AS service, d.sha, d.verdict, d.reason_code, d.price_pct::float8 AS price_pct,
       d.affordable_pct::float8 AS affordable_pct, d.canary_profile, d.inputs
FROM decisions d JOIN services s ON s.id = d.service_id WHERE d.id = :id
""")

STORE = text("""
UPDATE decisions SET brief_source = :source,
       inputs = jsonb_set(inputs, '{brief}', CAST(:brief AS jsonb))
WHERE id = :id
""")

LATEST_BLOCKS = text("""
SELECT DISTINCT ON (s.name) s.name AS service, d.id, d.verdict
FROM decisions d JOIN services s ON s.id = d.service_id
WHERE d.reason_code <> 'ADMIN_BYPASS'
ORDER BY s.name, d.decided_at DESC, d.id DESC
""")


def brief_context(row: Any, spec: ServiceLevel | None, policy: Policy, decision_url: str) -> dict[str, Any]:
    """Everything the brief may mention, from the decision record (and the spec's objective)."""
    inputs = row.inputs or {}
    facts, gi = inputs.get("facts") or {}, inputs.get("gate_input") or {}
    evidence, eta = inputs.get("lane_evidence") or {}, inputs.get("eta") or {}
    incident = evidence.get("incident_id")
    incident_id = f"INC-{incident}" if incident else None
    # Under charge_origin a page or over-budget block is self-inflicted by construction (the gate
    # only counts the service's own errors); an admit names no cause.
    culprit = row.service if row.verdict == "block" and row.reason_code in ("ACTIVE_PAGE", "OVER_BUDGET") \
        and policy.attribution == "charge_origin" else None
    still: list[str] = []
    if row.verdict == "block":
        still.append("rollback to a previously healthy build")
        if incident_id:
            still.append(f"a proven fix for {incident_id}")
    return {
        "lane": facts.get("lane"),
        "remaining_pct": gi.get("remaining_pct"),
        "reserve_pct": gi.get("reserve_pct", policy.reserve_pct),
        "objective_pct": 100 * spec.spec.slos[0].objective if spec else None,
        "burn_rate_1h": None,
        "culprit": culprit,
        "incident_id": incident_id,
        "eta_utc": eta.get("eta_utc"),
        "eta_in_hours": eta.get("eta_in_hours"),
        "still_allowed": still,
        "decision_url": decision_url,
    }


def decision_row_dict(row: Any) -> dict[str, Any]:
    return {"id": row.id, "service": row.service, "sha": row.sha, "verdict": row.verdict,
            "reason_code": row.reason_code, "price_pct": row.price_pct, "affordable_pct": row.affordable_pct,
            "canary_profile": list(row.canary_profile or [])}


async def pr_comment(http: httpx.AsyncClient, settings: Settings, inputs: dict[str, Any], body: str) -> bool:
    """Comment on the merged PR the decision priced (inputs.change.pr), with a pull_requests: write token."""
    pr = ((inputs.get("change") or {}).get("pr") or {}).get("number")
    installation = (inputs.get("github") or {}).get("installation_id")
    if not pr or installation != settings.github_installation_id:
        return False
    token = await installation_token(http, settings.github_installation_id, {"pull_requests": "write"})
    r = await http.post(f"https://api.github.com/repos/{settings.github_repo}/issues/{int(pr)}/comments",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                        json={"body": body[:60_000]}, timeout=10.0)
    r.raise_for_status()
    return True


async def handle(engine: AsyncEngine, writer: BriefWriter, http: httpx.AsyncClient, settings: Settings,
                 specs: dict[str, ServiceLevel], policy: Policy, decision_id: int) -> str:
    async with engine.connect() as c:
        row = (await c.execute(DECISION, {"id": decision_id})).first()
    if row is None:
        return "no such decision"
    url = f"{settings.public_url}/decisions/{row.id}"
    view = brief_view(decision_row_dict(row), brief_context(row, specs.get(row.service), policy, url))
    result = await writer.write(view)
    brief = {k: v for k, v in asdict(result).items() if k in ("text", "source", "fallback_reason", "model",
                                                               "prompt_sha", "latency_ms", "cost_usd")}
    async with engine.begin() as c:
        await c.execute(STORE, {"id": row.id, "source": result.source, "brief": json.dumps(brief)})
    delivered = []
    for name, send in (("slack", slack.post(http, settings.slack_briefs_url, result.text)),
                       ("pr", pr_comment(http, settings, row.inputs or {}, result.text))):
        try:
            if await send:
                delivered.append(name)
        except httpx.HTTPError as e:
            log.warning("brief %s: %s delivery failed (%s)", row.id, name, type(e).__name__)
    where = ", ".join(delivered) or "stored only"
    return f"{result.source} ({result.fallback_reason or result.model}) -> {where}"


async def recoveries(engine: AsyncEngine, valkey: Any, http: httpx.AsyncClient, settings: Settings,
                     specs: dict[str, ServiceLevel], policy: Policy) -> list[str]:
    """'Budget recovered' (W7.6): the latest decision blocked, and the gate's numbers now admit."""
    from ..gather import gather_signals
    from ..integrations.prometheus import PrometheusClient
    async with engine.connect() as c:
        rows = [r for r in (await c.execute(LATEST_BLOCKS)).all() if r.verdict == "block"]
    prom = PrometheusClient(settings.prometheus_url, http, settings.prometheus_timeout_s)
    sent = []
    for r in rows:
        sl = specs.get(r.service)
        if sl is None:
            continue
        sig = await gather_signals(prom, engine, sl, datetime.now(UTC))
        remaining = sig.remaining_self_pct if policy.attribution == "charge_origin" else sig.remaining_all_pct
        self_page = sig.page_firing and (sig.self_share_1h or 0.0) >= policy.self_page_share
        if remaining is None or self_page or remaining < policy.reserve_pct:
            continue
        if not await valkey.set(f"bg:recovered:{r.id}", "1", nx=True, ex=30 * 86400):
            continue                                         # already announced for this block
        url = f"{settings.public_url}/decisions/{r.id}"
        await slack.post(http, settings.slack_briefs_url, recovered_brief(r.service, remaining,
                                                                          policy.reserve_pct, url))
        sent.append(r.service)
    return sent


async def run(consumer: str) -> None:
    from redis.asyncio import Redis
    from redis.exceptions import ResponseError

    from ..db import make_engine
    from .gate_worker import load_policy, load_specs

    settings = get_settings()
    policy, specs = load_policy(settings), load_specs(settings)
    cfg = LlmConfig.load(settings.llm_config)
    try:
        model = build_model(cfg.provider, cfg.model)
    except Exception as e:                                   # noqa: BLE001 - no SDK or no key: template only
        log.warning("provider %s unavailable (%s): briefs use the template", cfg.provider, type(e).__name__)
        model = None
    valkey = Redis.from_url(settings.valkey_url, decode_responses=True, socket_timeout=15,
                            socket_connect_timeout=5, health_check_interval=30)
    try:
        await valkey.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    writer = BriefWriter(cfg, model, ValkeySpendLedger(valkey), load_prompt(settings.prompt_dir), check)
    engine = make_engine(pool_size=2)
    last_recovery = 0.0
    log.info("brief writer %s: provider %s, prompt %s", consumer, cfg.provider if model else "template",
             writer.prompt.sha)
    async with httpx.AsyncClient(timeout=10.0) as http:
        while True:
            auto = await valkey.xautoclaim(STREAM, GROUP, consumer, 60_000, "0-0", count=10)
            fresh: Any = await valkey.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=10, block=5000)
            for entry_id, fields in list(auto[1]) + [e for _, es in (fresh or []) for e in es]:
                try:
                    out = await handle(engine, writer, http, settings, specs, policy,
                                       int(fields["decision_id"]))
                    log.info("brief for decision %s: %s", fields["decision_id"], out)
                except DB_DOWN:
                    break                                    # stays pending; XAUTOCLAIM retries
                except (KeyError, ValueError) as e:
                    log.warning("dropping malformed entry %s: %s", entry_id, e)
                await valkey.xack(STREAM, GROUP, entry_id)
            loop = asyncio.get_running_loop()
            if loop.time() - last_recovery >= RECOVERY_EVERY_S:
                try:
                    if sent := await recoveries(engine, valkey, http, settings, specs, policy):
                        log.info("budget recovered: %s", sent)
                except RECOVERY_SKIP as e:
                    log.warning("recovery check skipped: %s", type(e).__name__)
                last_recovery = loop.time()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brief_writer")
    ap.add_argument("--consumer", default=socket.gethostname())
    a = ap.parse_args(argv)
    redact.install()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(a.consumer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
