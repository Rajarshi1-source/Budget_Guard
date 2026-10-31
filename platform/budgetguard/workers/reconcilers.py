"""Reconcilers (W5.9; security-and-api.md T-1, T-4, change #11).

    python -m budgetguard.workers.reconcilers [--once]

redeliver   GitHub never redelivers a failed webhook by itself. Every 5 minutes, list the App's
            recent deliveries and redeliver each deployment_protection_rule delivery that failed
            (no 2xx attempt for its guid) and has no decision, so a gate outage does not strand a
            waiting run.
bypass      An admin can skip a protection rule. Any production deployment that actually ran
            (latest status success or in_progress, older than 15 minutes) with no decision for its
            deployment id is recorded as an override by 'github-admin-bypass': a synthetic
            ADMIN_BYPASS decision plus an overrides row, so the audit trail and the backtester see it.

Environment drift and cluster digest drift (change #12) arrive in W8.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .. import redact
from ..config import Settings, get_settings
from ..integrations.github_app import API, app_jwt

log = logging.getLogger("budgetguard.reconcilers")

REDELIVER_WINDOW = timedelta(days=3)      # GitHub keeps deliveries for 3 days
BYPASS_GRACE = timedelta(minutes=15)      # a fresh deployment may still be waiting for our answer
BYPASS_REASON = "Deployment ran without a BudgetGuard decision (GitHub admin bypass of the protection rule)"


class Ledger(Protocol):
    """What the reconcilers need from PostgreSQL."""
    async def decided_deliveries(self, guids: list[str]) -> set[str]: ...
    async def decided_deployments(self, ids: list[int]) -> set[int]: ...
    async def record_bypass(self, service: str, deployment: dict[str, Any], policy_version: str) -> bool: ...


@dataclass
class GitHub:
    """GitHub calls of the reconcilers: the App JWT for /app/hook/*, a deployments:read
    installation token for the repository's deployments."""
    http: httpx.AsyncClient
    settings: Settings
    jwt: Callable[[], str] = field(default=app_jwt)

    def _app(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.jwt()}", "Accept": "application/vnd.github+json"}

    async def _token(self, permissions: dict[str, str]) -> str:
        """Installation token down-scoped to this repository (same request as installation_token)."""
        installation = self.settings.github_installation_id
        r = await self.http.post(f"{API}/app/installations/{installation}/access_tokens",
                                 headers=self._app(),
                                 json={"repositories": [self.settings.github_repo.split("/", 1)[1]],
                                       "permissions": permissions})
        r.raise_for_status()
        token: str = r.json()["token"]
        return token

    async def deliveries(self) -> list[dict[str, Any]]:
        r = await self.http.get(f"{API}/app/hook/deliveries", params={"per_page": 100}, headers=self._app())
        r.raise_for_status()
        body: list[dict[str, Any]] = r.json()
        return body

    async def redeliver(self, delivery_id: int) -> None:
        r = await self.http.post(f"{API}/app/hook/deliveries/{delivery_id}/attempts", headers=self._app())
        r.raise_for_status()

    async def deployments(self, environment: str) -> list[dict[str, Any]]:
        token = await self._token({"deployments": "read"})
        h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        repo = self.settings.github_repo
        r = await self.http.get(f"{API}/repos/{repo}/deployments",
                                params={"environment": environment, "per_page": 30}, headers=h)
        r.raise_for_status()
        out: list[dict[str, Any]] = r.json()
        for d in out:
            s = await self.http.get(f"{API}/repos/{repo}/deployments/{d['id']}/statuses",
                                    params={"per_page": 1}, headers=h)
            s.raise_for_status()
            statuses = s.json()
            d["latest_state"] = statuses[0]["state"] if statuses else None
        return out


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def redeliver_failed(gh: GitHub, ledger: Ledger, now: datetime) -> list[str]:
    deliveries = [d for d in await gh.deliveries() if d.get("event") == "deployment_protection_rule"
                  and _ts(d["delivered_at"]) > now - REDELIVER_WINDOW]
    ok = {d["guid"] for d in deliveries if 200 <= int(d.get("status_code") or 0) < 300}
    failed: dict[str, dict[str, Any]] = {}
    for d in deliveries:                                  # newest attempt per guid wins
        if d["guid"] not in ok and d["guid"] not in failed:
            failed[d["guid"]] = d
    decided = await ledger.decided_deliveries(list(failed))
    redone = []
    for guid, d in failed.items():
        if guid in decided:
            continue
        await gh.redeliver(int(d["id"]))
        redone.append(guid)
    return redone


async def ingest_bypasses(gh: GitHub, ledger: Ledger, services: list[str], policy_version: str,
                          now: datetime) -> list[int]:
    recorded = []
    for service in services:
        deps = [d for d in await gh.deployments(f"production-{service}")
                if d.get("latest_state") in ("success", "in_progress")
                and _ts(d["created_at"]) < now - BYPASS_GRACE]
        known = await ledger.decided_deployments([int(d["id"]) for d in deps])
        for d in deps:
            if int(d["id"]) not in known and await ledger.record_bypass(service, d, policy_version):
                log.warning("admin bypass recorded: %s deployment %s (%s)", service, d["id"], d["sha"][:7])
                recorded.append(int(d["id"]))
    return recorded


class PgLedger:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def decided_deliveries(self, guids: list[str]) -> set[str]:
        if not guids:
            return set()
        async with self.engine.connect() as c:
            rows = await c.execute(text("SELECT github_delivery FROM decisions "
                                        "WHERE github_delivery = ANY(:g)"),
                                   {"g": guids})
            return {r[0] for r in rows}

    async def decided_deployments(self, ids: list[int]) -> set[int]:
        if not ids:
            return set()
        async with self.engine.connect() as c:
            rows = await c.execute(text("SELECT github_deployment_id FROM decisions "
                                        "WHERE github_deployment_id = ANY(:i)"), {"i": ids})
            return {int(r[0]) for r in rows}

    async def record_bypass(self, service: str, deployment: dict[str, Any], policy_version: str) -> bool:
        inputs = {"bypass": True, "github": {"deployment_id": deployment["id"], "sha": deployment["sha"],
                                              "environment": deployment.get("environment"),
                                              "creator": (deployment.get("creator") or {}).get("login")}}
        digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        async with self.engine.begin() as c:
            row = (await c.execute(text("""
                INSERT INTO decisions (service_id, github_delivery, github_deployment_id, sha, policy_version,
                                       inputs_hash, inputs, verdict, reason_code, canary_profile,
                                       callback_state)
                SELECT s.id, :delivery, :dep, :sha, :pv, :h, CAST(:inputs AS jsonb), 'admit', 'ADMIN_BYPASS',
                       '{}', 'sent'
                FROM services s WHERE s.name = :service
                ON CONFLICT DO NOTHING RETURNING id"""),
                {"delivery": f"bypass:{deployment['id']}", "dep": int(deployment["id"]),
                 "sha": deployment["sha"], "pv": policy_version, "h": digest, "inputs": json.dumps(inputs),
                 "service": service})).first()
            if row is None:
                return False
            await c.execute(text("""
                INSERT INTO overrides (decision_id, approver, reason, consumed_by_decision_id)
                VALUES (:d, 'github-admin-bypass', :r, :d)"""), {"d": row.id, "r": BYPASS_REASON})
        return True


async def run(once: bool) -> None:
    from ..db import make_engine
    from ..rulegen.spec import load_dir
    from .gate_worker import ensure_policy, load_policy

    settings = get_settings()
    services = [sl.metadata.service for sl in load_dir(settings.slo_dir)]
    engine = make_engine(pool_size=2)
    policy = load_policy(settings)
    await ensure_policy(engine, policy)
    ledger = PgLedger(engine)
    async with httpx.AsyncClient(timeout=10.0) as http:
        gh = GitHub(http, settings)
        while True:
            now = datetime.now(UTC)
            for name, job in (("redeliver", redeliver_failed(gh, ledger, now)),
                              ("bypass", ingest_bypasses(gh, ledger, services, policy.version, now))):
                try:
                    done = await job
                    if done:
                        log.info("%s: %s", name, done)
                except (httpx.HTTPError, OSError) as e:
                    log.warning("%s failed: %s", name, type(e).__name__)
            if once:
                break
            await asyncio.sleep(300)
    await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reconcilers")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    redact.install()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(a.once))
    return 0


if __name__ == "__main__":
    sys.exit(main())
