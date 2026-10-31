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

environment Every 5 minutes, read each production-<service> environment: it must exist, have
            admin bypass off, and carry this App's custom protection rule, enabled. Anything else
            sets bg_gate_config_drift{service,reason} = 1, which pages GateConfigDrift (W8.5).
cluster     Each Rollout in bg-apps carries the commit and image digest the deploy job set (pod
            template annotations). Drift is a live image digest that differs from the annotation
            (`kubectl set image`), or an annotated commit with no admitted decision for the service
            (a hand-run `helm upgrade`). Each finding is recorded once as an override by
            'cluster-admin-drift' and sets bg_cluster_digest_drift{service} = 1 (ClusterDigestDrift).
            Off outside Kubernetes (no service-account token).

The environment and cluster checks make a bypass visible; a cluster admin can still do anything
(security-and-api.md T-4 says "mitigated by audit, not prevented").
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
from pathlib import Path
from typing import Any, Protocol

import httpx
from prometheus_client import Gauge, start_http_server
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .. import redact
from ..config import Settings, get_settings
from ..integrations.github_app import API, app_jwt

log = logging.getLogger("budgetguard.reconcilers")

REDELIVER_WINDOW = timedelta(days=3)      # GitHub keeps deliveries for 3 days
BYPASS_GRACE = timedelta(minutes=15)      # a fresh deployment may still be waiting for our answer
BYPASS_REASON = "Deployment ran without a BudgetGuard decision (GitHub admin bypass of the protection rule)"
DRIFT_REASON = ("Running image was not deployed by an admitted deploy job "
                "(cluster changed outside the pipeline)")
SHA_ANNOTATION = "budgetguard.dev/git-sha"
DIGEST_ANNOTATION = "budgetguard.dev/image-digest"
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")

CONFIG_DRIFT = Gauge("bg_gate_config_drift", "1 while a production environment does not enforce the gate",
                     ["service", "reason"])
DIGEST_DRIFT = Gauge("bg_cluster_digest_drift", "1 while a Rollout runs an image no admitted deploy set",
                     ["service"])
LAST_SUCCESS = Gauge("bg_reconciler_last_success_timestamp_seconds", "Last clean run of each check",
                     ["check"])


class Ledger(Protocol):
    """What the reconcilers need from PostgreSQL."""
    async def decided_deliveries(self, guids: list[str]) -> set[str]: ...
    async def decided_deployments(self, ids: list[int]) -> set[int]: ...
    async def record_bypass(self, service: str, deployment: dict[str, Any], policy_version: str) -> bool: ...
    async def admitted_shas(self, service: str, shas: list[str]) -> set[str]: ...
    async def record_drift(self, finding: DigestDrift, policy_version: str) -> bool: ...


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

    async def environment(self, environment: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """(environment, its custom protection rules); (None, []) when the environment is gone."""
        token = await self._token({"actions": "read"})
        h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        base = f"{API}/repos/{self.settings.github_repo}/environments/{environment}"
        r = await self.http.get(base, headers=h)
        if r.status_code == 404:
            return None, []
        r.raise_for_status()
        rules = await self.http.get(f"{base}/deployment_protection_rules", headers=h)
        rules.raise_for_status()
        custom: list[dict[str, Any]] = rules.json().get("custom_deployment_protection_rules", [])
        env: dict[str, Any] = r.json()
        return env, custom


@dataclass
class Kube:
    """Read-only Kubernetes calls: list Rollouts in one namespace (Role in the platform chart)."""
    http: httpx.AsyncClient
    namespace: str

    @classmethod
    def in_cluster(cls, namespace: str, sa_dir: Path = SA_DIR) -> Kube | None:
        token = sa_dir / "token"
        if not token.exists():
            return None
        http = httpx.AsyncClient(base_url="https://kubernetes.default.svc", timeout=10.0,
                                 verify=str(sa_dir / "ca.crt"),
                                 headers={"Authorization": f"Bearer {token.read_text().strip()}"})
        return cls(http, namespace)

    async def rollouts(self) -> list[dict[str, Any]]:
        r = await self.http.get(f"/apis/argoproj.io/v1alpha1/namespaces/{self.namespace}/rollouts")
        r.raise_for_status()
        items: list[dict[str, Any]] = r.json().get("items", [])
        return items


@dataclass(frozen=True)
class DigestDrift:
    service: str
    live_digest: str | None
    annotated_digest: str | None
    sha: str | None
    reason: str                     # image_changed | sha_not_admitted | unannotated


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


def environment_drift(env: dict[str, Any] | None, rules: list[dict[str, Any]], app_id: str) -> list[str]:
    """Why this environment does not enforce the gate; [] when it does."""
    if env is None:
        return ["environment_missing"]
    reasons = []
    if env.get("can_admins_bypass", True):           # absent = GitHub's default, which is True
        reasons.append("admin_bypass_enabled")
    ours = [r for r in rules if str((r.get("app") or {}).get("id")) == str(app_id)]
    if not ours:
        reasons.append("protection_rule_missing")
    elif not any(r.get("enabled") for r in ours):
        reasons.append("protection_rule_disabled")
    return reasons


async def check_environments(gh: GitHub, services: list[str], app_id: str) -> dict[str, list[str]]:
    found = {}
    for service in services:
        env, rules = await gh.environment(f"production-{service}")
        found[service] = environment_drift(env, rules, app_id)
    return found


def _digest(image: str) -> str | None:
    return image.rsplit("@", 1)[1] if "@" in image else None


def rollout_drift(rollout: dict[str, Any], admitted: set[str]) -> DigestDrift | None:
    """Compare what runs with what the deploy job stamped on it. `admitted` = commits with an
    admitted decision for this service (the caller asks the ledger for the annotated one)."""
    service = rollout["metadata"]["name"]
    template = rollout["spec"]["template"]
    ann = (template.get("metadata") or {}).get("annotations") or {}
    app = next((c for c in template["spec"]["containers"] if c["name"] == "app"),
               template["spec"]["containers"][0])
    live, stamped, sha = _digest(app["image"]), ann.get(DIGEST_ANNOTATION), ann.get(SHA_ANNOTATION)
    if not stamped or not sha:
        reason = "unannotated"
    elif live != stamped:
        reason = "image_changed"
    elif sha not in admitted:
        reason = "sha_not_admitted"
    else:
        return None
    return DigestDrift(service, live, stamped, sha, reason)


async def check_cluster(kube: Kube, ledger: Ledger, policy_version: str) -> list[DigestDrift]:
    findings = []
    for ro in await kube.rollouts():
        service = ro["metadata"]["name"]
        sha = ((ro["spec"]["template"].get("metadata") or {}).get("annotations") or {}).get(SHA_ANNOTATION)
        admitted = await ledger.admitted_shas(service, [sha] if sha else [])
        finding = rollout_drift(ro, admitted)
        if finding is None:
            continue
        findings.append(finding)
        if await ledger.record_drift(finding, policy_version):
            log.warning("cluster drift recorded: %s %s (live %s, stamped %s)", service, finding.reason,
                        (finding.live_digest or "-")[:19], (finding.annotated_digest or "-")[:19])
    return findings


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

    async def admitted_shas(self, service: str, shas: list[str]) -> set[str]:
        if not shas:
            return set()
        async with self.engine.connect() as c:
            rows = await c.execute(text("""
                SELECT DISTINCT d.sha FROM decisions d JOIN services s ON s.id = d.service_id
                WHERE s.name = :service AND d.sha = ANY(:shas)
                  AND d.verdict IN ('admit', 'admit_unverified') AND d.reason_code <> 'CLUSTER_DRIFT'"""),
                {"service": service, "shas": shas})
            return {r[0] for r in rows}

    async def record_drift(self, finding: DigestDrift, policy_version: str) -> bool:
        """One synthetic CLUSTER_DRIFT decision + override per (service, live digest, commit)."""
        inputs = {"cluster_drift": True, "reason": finding.reason, "live_digest": finding.live_digest,
                  "annotated_digest": finding.annotated_digest, "sha": finding.sha}
        digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        async with self.engine.begin() as c:
            row = (await c.execute(text("""
                INSERT INTO decisions (service_id, github_delivery, sha, policy_version, inputs_hash, inputs,
                                       verdict, reason_code, canary_profile, callback_state)
                SELECT s.id, :delivery, :sha, :pv, :h, CAST(:inputs AS jsonb), 'admit', 'CLUSTER_DRIFT',
                       '{}', 'sent'
                FROM services s WHERE s.name = :service
                ON CONFLICT DO NOTHING RETURNING id"""),
                {"delivery": f"drift:{finding.service}:{digest[:32]}", "sha": finding.sha or "0" * 40,
                 "pv": policy_version, "h": digest, "inputs": json.dumps(inputs),
                 "service": finding.service})).first()
            if row is None:
                return False
            await c.execute(text("""
                INSERT INTO overrides (decision_id, approver, reason, consumed_by_decision_id)
                VALUES (:d, 'cluster-admin-drift', :r, :d)"""), {"d": row.id, "r": DRIFT_REASON})
        return True


def _publish_environments(found: dict[str, list[str]]) -> None:
    CONFIG_DRIFT.clear()
    for service, reasons in found.items():
        for reason in reasons:
            CONFIG_DRIFT.labels(service, reason).set(1)
            log.warning("gate config drift: production-%s %s", service, reason)


def _publish_cluster(findings: list[DigestDrift], services: list[str]) -> None:
    DIGEST_DRIFT.clear()
    drifted = {f.service for f in findings}
    for service in services:
        DIGEST_DRIFT.labels(service).set(1 if service in drifted else 0)


async def run(once: bool, metrics_port: int | None = None) -> None:
    from ..db import make_engine
    from ..rulegen.spec import load_dir
    from .gate_worker import ensure_policy, load_policy

    settings = get_settings()
    services = [sl.metadata.service for sl in load_dir(settings.slo_dir)]
    engine = make_engine(pool_size=2)
    policy = load_policy(settings)
    await ensure_policy(engine, policy)
    ledger = PgLedger(engine)
    if metrics_port is not None:
        start_http_server(metrics_port)
    kube = Kube.in_cluster(settings.apps_namespace)
    if kube is None:
        log.info("no service-account token: cluster digest drift check is off")
    async with httpx.AsyncClient(timeout=10.0) as http:
        gh = GitHub(http, settings)
        while True:
            now = datetime.now(UTC)
            jobs: list[tuple[str, Any]] = [
                ("redeliver", redeliver_failed(gh, ledger, now)),
                ("bypass", ingest_bypasses(gh, ledger, services, policy.version, now)),
                ("environment", check_environments(gh, services, settings.github_app_id))]
            if kube is not None:
                jobs.append(("cluster", check_cluster(kube, ledger, policy.version)))
            for name, job in jobs:
                try:
                    done = await job
                except (httpx.HTTPError, OSError) as e:      # a failed check keeps its last gauges
                    log.warning("%s failed: %s", name, type(e).__name__)
                    continue
                if name == "environment":
                    _publish_environments(done)
                elif name == "cluster":
                    _publish_cluster(done, services)
                elif done:
                    log.info("%s: %s", name, done)
                LAST_SUCCESS.labels(name).set(now.timestamp())
            if once:
                break
            await asyncio.sleep(300)
    if kube is not None:
        await kube.http.aclose()
    await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reconcilers")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--metrics-port", type=int, default=None)
    a = ap.parse_args(argv)
    redact.install()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(a.once, a.metrics_port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
