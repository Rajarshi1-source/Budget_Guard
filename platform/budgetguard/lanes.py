"""Lane proofs (W5.2, budgetguard-admission-gate §4). A lane is proven, never claimed: a label is
self-asserted by the person who benefits from it, so it is not a security boundary.

  rollback  the deploy's services/<service> git subtree SHA equals the subtree of an earlier
            deployment with status success and is_change_failure false (query 4.8). A revert or a
            re-run of an old commit produce a matching tree; a "rollback" that adds code cannot.
  override  an unexpired, unconsumed overrides row for this service and SHA (query 4.10a), locked
            FOR UPDATE SKIP LOCKED and consumed in the same transaction as the new decision.
  incident_fix  an open incident on this service whose alert is firing, referenced by a PR of at most
            400 lines; or a Dependabot/Renovate PR citing a security advisory (W6.4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .changes import PullRequest
from .gate import Lane


class TreeReader(Protocol):
    async def get(self, path: str) -> dict[str, Any]: ...


async def service_tree_sha(gh: TreeReader, repo: str, sha: str, service: str) -> str | None:
    """Git tree object of services/<service> at `sha`, via the git trees API (no checkout)."""
    commit = await gh.get(f"/repos/{repo}/git/commits/{sha}")
    tree_sha: str = commit["tree"]["sha"]
    for part in ("services", service):
        tree = await gh.get(f"/repos/{repo}/git/trees/{tree_sha}")
        entry = next((e for e in tree["tree"] if e["path"] == part and e["type"] == "tree"), None)
        if entry is None:
            return None
        tree_sha = entry["sha"]
    return tree_sha


ROLLBACK_PROOF = text("""
SELECT d.id, d.sha, d.finished_at
FROM deployments d JOIN services s ON s.id = d.service_id
WHERE s.name = :service AND d.service_tree_sha = :tree_sha
  AND d.status = 'success' AND d.is_change_failure IS FALSE
ORDER BY d.finished_at DESC LIMIT 1
""")

OVERRIDE_PROOF = text("""
SELECT o.id, o.decision_id, o.approver
FROM overrides o
JOIN decisions blocked ON blocked.id = o.decision_id
JOIN services s ON s.id = blocked.service_id
WHERE s.name = :service AND blocked.sha = :sha
  AND o.expires_at > now() AND o.consumed_by_decision_id IS NULL
ORDER BY o.created_at DESC
LIMIT 1
FOR UPDATE OF o SKIP LOCKED
""")


@dataclass(frozen=True, slots=True)
class LaneProof:
    lane: Lane | None
    evidence: dict[str, Any] = field(default_factory=dict)
    override_id: int | None = None          # consume in the decision's transaction


async def rollback_proof(conn: AsyncConnection, service: str, tree_sha: str | None) -> LaneProof:
    if tree_sha is None:
        return LaneProof(None, {"service_tree_sha": None})
    row = (await conn.execute(ROLLBACK_PROOF, {"service": service, "tree_sha": tree_sha})).first()
    if row is None:
        return LaneProof(None, {"service_tree_sha": tree_sha})
    return LaneProof(Lane.ROLLBACK, {"service_tree_sha": tree_sha, "matched_deployment_id": row.id,
                                     "matched_sha": row.sha,
                                     "matched_finished_at":
                                         row.finished_at.isoformat() if row.finished_at else None})


async def override_proof(conn: AsyncConnection, service: str, sha: str) -> LaneProof:
    """Must run inside the transaction that inserts the decision: the row lock is what stops two
    concurrent re-runs from both consuming one override."""
    row = (await conn.execute(OVERRIDE_PROOF, {"service": service, "sha": sha})).first()
    if row is None:
        return LaneProof(None)
    return LaneProof(Lane.OVERRIDE, {"override_id": row.id, "overridden_decision_id": row.decision_id,
                                     "approver": row.approver}, override_id=row.id)


async def consume_override(conn: AsyncConnection, override_id: int, decision_id: int) -> None:
    await conn.execute(text("UPDATE overrides SET consumed_by_decision_id = :d WHERE id = :o"),
                       {"d": decision_id, "o": override_id})


# ---------------------------------------------------------------- incident-fix and security lanes (W6.4)
INCIDENT_REF = re.compile(r"\bfixes\s+BG-INC-(\d{1,12})\b", re.IGNORECASE)
ADVISORY = re.compile(r"\bGHSA(?:-[23456789cfghjmpqrvwx]{4}){3}\b", re.IGNORECASE)
ALERTNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")      # before it is placed in PromQL
SECURITY_BOTS = frozenset({"dependabot[bot]", "renovate[bot]"})
INCIDENT_FIX_MAX_LINES = 400

OPEN_INCIDENT = text("""
SELECT i.id, i.alertname
FROM incidents i JOIN services s ON s.id = i.service_id
WHERE i.id = :id AND s.name = :service AND i.resolved_at IS NULL
""")


class AlertReader(Protocol):
    async def query(self, expr: str) -> list[dict[str, Any]]: ...


async def incident_fix_proof(conn: AsyncConnection, prom: AlertReader, service: str,
                             pr: PullRequest | None) -> LaneProof:
    """Incident fix: the PR says `Fixes BG-INC-<id>`, that incident is open on THIS service, its
    alertname is firing for this service in Prometheus right now (an incident row alone can be forged
    through the Alertmanager webhook, security-and-api.md T-6), and the PR is at most 400 lines.
    Security: a Dependabot/Renovate PR that cites a GitHub security advisory; treated as a fix."""
    if pr is None:
        return LaneProof(None)
    if pr.author in SECURITY_BOTS and (adv := ADVISORY.search(pr.body)):
        return LaneProof(Lane.INCIDENT_FIX, {"kind": "security", "author": pr.author,
                                             "advisory": adv.group(0), "pr": pr.number})
    ref = INCIDENT_REF.search(pr.body)
    if ref is None:
        return LaneProof(None)
    evidence: dict[str, Any] = {"kind": "incident_fix", "incident_id": int(ref.group(1)), "pr": pr.number,
                                "pr_lines": pr.total_lines}
    if pr.total_lines > INCIDENT_FIX_MAX_LINES:
        why = f"PR has {pr.total_lines} lines (max {INCIDENT_FIX_MAX_LINES})"
        return LaneProof(None, {**evidence, "denied": why})
    row = (await conn.execute(OPEN_INCIDENT, {"id": int(ref.group(1)), "service": service})).first()
    if row is None:
        return LaneProof(None, {**evidence, "denied": "no open incident with that id on this service"})
    if not ALERTNAME.match(row.alertname):
        return LaneProof(None, {**evidence, "denied": "incident alertname is not a valid metric name"})
    sel = f'alertstate="firing",alertname="{row.alertname}",service="{service}"'
    firing = await prom.query(f"ALERTS{{{sel}}}")
    if not firing:
        return LaneProof(None, {**evidence, "alertname": row.alertname, "denied": "alert is not firing"})
    return LaneProof(Lane.INCIDENT_FIX, {**evidence, "alertname": row.alertname})
