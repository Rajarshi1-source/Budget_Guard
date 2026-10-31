"""Builds the 120-case golden set for the Freeze Brief eval. Seeded and committed as JSONL.

Composition (Rev 2 §10.5): 40 admit, 40 block, 20 lanes, 20 unknown. Inside those, adversarial
slices that catch the failures we care about: culprit null (model must not invent a cause),
overspent budget (negative remaining), dependency culprit (victim admitted), no ETA, all-digit
SHA prefixes, and long still-allowed lists.

Usage: python -m evals.make_golden > evals/golden/briefs.jsonl
"""
from __future__ import annotations

import json
import random
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from budgetguard.briefs import brief_view

SERVICES = ("auth-service", "orders-service", "inventory-service", "notifications-service")
OBJECTIVE = {"auth-service": 99.95, "orders-service": 99.9, "inventory-service": 99.5,
             "notifications-service": 99.0}
NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


def _sha(rng: random.Random, digits_only: bool = False) -> str:
    alphabet = "0123456789" if digits_only else "0123456789abcdef"
    head = "".join(rng.choice(alphabet) for _ in range(7))
    return head + "".join(rng.choice("0123456789abcdef") for _ in range(33))


def _case(rng: random.Random, i: int, kind: str) -> dict[str, Any]:
    service = rng.choice(SERVICES)
    start = round(rng.uniform(-25, 95), 2)
    reserve = 5.0
    remaining: float | None = start
    afford: float | None = round(max(0.0, 0.5 * (start - reserve)), 2)
    culprit: str | None = service
    incident: str | None = None
    lane: str | None = None
    eta_hours: float | None = None
    still: list[str] = ["rollback to a previously healthy build"]
    price: float | None = round(rng.uniform(0.1, 4.0), 2)
    profile = [25, 100]
    if kind == "admit":
        verdict, reason = "admit", "WITHIN_BUDGET"
        remaining = round(rng.uniform(12, 95), 2)
        afford = round(0.5 * (remaining - reserve), 2)
        price = round(rng.uniform(0.05, max(0.06, afford)), 2)
        culprit = None if i % 2 else rng.choice([s for s in SERVICES if s != service])
        profile = [25, 100] if remaining >= 50 else [10, 50, 100] if remaining >= 20 else [5, 25, 50, 100]
    elif kind == "block":
        verdict = "block"
        if i % 3 == 0:
            reason, price, afford = "ACTIVE_PAGE", None, None
            incident = f"INC-{1000 + i}"
            still.append(f"a proven fix for {incident}")
        else:
            reason = "OVER_BUDGET"                       # realistic: blocks happen when budget is short
            remaining = round(rng.uniform(-25, 12), 2)
            afford = round(max(0.0, 0.5 * (remaining - reserve)), 2)
            price = round(afford + rng.uniform(0.05, 2.0), 2)
        culprit = None if i % 4 == 1 else service
        eta_hours = None if i % 5 == 0 else round(rng.uniform(0.5, 160), 1)
        profile = []
    elif kind == "lane":
        verdict = "admit"
        lane = "rollback" if i % 2 else "incident_fix"
        reason = "ROLLBACK_LANE" if lane == "rollback" else "INCIDENT_FIX_LANE"
        incident = f"INC-{2000 + i}"
        price = 0.0 if lane == "rollback" else price
        afford = None
        profile = [100] if lane == "rollback" else [5, 25, 50, 100]
    else:  # unknown
        fail_closed = i % 4 == 0
        verdict = "block" if fail_closed else "admit_unverified"
        reason, price, afford, remaining, culprit = "SLI_UNKNOWN", None, None, None, None
        profile = [] if fail_closed else [5, 25, 50, 100]
    eta_utc = (NOW + timedelta(hours=eta_hours)).strftime("%Y-%m-%dT%H:%MZ") if eta_hours else None
    decision = {"id": 4000 + i, "service": service, "sha": _sha(rng, digits_only=i % 17 == 0),
                "verdict": verdict, "reason_code": reason, "price_pct": price,
                "affordable_pct": afford, "canary_profile": profile}
    ctx = {"lane": lane, "remaining_pct": remaining, "reserve_pct": reserve,
           "objective_pct": OBJECTIVE[service], "burn_rate_1h": round(rng.uniform(0, 20), 1),
           "culprit": culprit, "incident_id": incident, "eta_utc": eta_utc, "eta_in_hours": eta_hours,
           "still_allowed": still, "decision_url": f"https://budgetguard.example.dev/decisions/{4000 + i}"}
    return {"id": f"g{i:03d}", "kind": kind, "view": brief_view(decision, ctx)}


def build(seed: int = 20261024) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    plan = ["admit"] * 40 + ["block"] * 40 + ["lane"] * 20 + ["unknown"] * 20
    return [_case(rng, i, kind) for i, kind in enumerate(plan)]


if __name__ == "__main__":
    for row in build():
        sys.stdout.write(json.dumps(row, sort_keys=True) + "\n")
