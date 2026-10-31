"""Lane proofs (W5.2, budgetguard-admission-gate §4). A lane is proven, never claimed: a label is
self-asserted by the person who benefits from it, so it is not a security boundary.

  rollback  the deploy's services/<service> git subtree SHA equals the subtree of an earlier
            deployment with status success and is_change_failure false (query 4.8). A revert or a
            re-run of an old commit produce a matching tree; a "rollback" that adds code cannot.
  override  an unexpired, unconsumed overrides row for this service and SHA (query 4.10a), locked
            FOR UPDATE SKIP LOCKED and consumed in the same transaction as the new decision.
  incident_fix  arrives in W6.4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

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
