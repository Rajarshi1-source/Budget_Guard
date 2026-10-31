"""Chain-of-responsibility gate (budgetguard-admission-gate §2). Deterministic: the same GateInput
always gives the same Decision (D-8), so a decision recomputes from its stored inputs and the
backtester replays this exact code.

Chain (plan A-06): rollback -> override -> integrity -> incident_fix -> hard_stop -> price.
Rollback runs before integrity so a fail-closed service can always roll back; the override lane
sits right after it so an approver can break glass while the SLI is UNKNOWN.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal

from .integrity import Integrity
from .pricing import affordable_pct

CONSERVATIVE = (5, 25, 50, 100)


class Verdict(StrEnum):
    ADMIT = "admit"
    ADMIT_UNVERIFIED = "admit_unverified"   # fail-open: admitted, audited, on-call paged
    BLOCK = "block"


class Lane(StrEnum):
    ROLLBACK = "rollback"
    INCIDENT_FIX = "incident_fix"
    OVERRIDE = "override"          # an unexpired, unused override row for this service and SHA


@dataclass(frozen=True, slots=True)
class GateInput:
    service: str
    policy_version: str
    integrity: Integrity
    remaining_pct: float | None            # 30-day, from buckets; may be negative
    active_page: bool                      # a page burn alert firing (self-inflicted: charge_origin)
    lane: Lane | None                      # already PROVEN by lanes.py, never a raw label
    price_pct: float
    reserve_pct: float
    spend_fraction: float
    failure_mode: Literal["fail_open", "fail_closed"]
    low_traffic_prices_normally: bool = True   # False = Rev 2 behaviour (LOW_TRAFFIC treated as UNKNOWN)

    def digest(self) -> str:
        """sha256 of canonical JSON: sorted keys, enums as their values. Stable across processes."""
        canonical = json.dumps(asdict(self), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    reason_code: str
    reason: str
    price_pct: float | None = None
    affordable_pct: float | None = None
    canary_profile: tuple[int, ...] = (25, 100)


Rule = Callable[[GateInput], Decision | None]


def canary_profile(remaining_pct: float | None) -> tuple[int, ...]:
    if remaining_pct is None or remaining_pct < 20:
        return CONSERVATIVE
    if remaining_pct < 50:
        return (10, 50, 100)
    return (25, 100)


def rollback_lane(i: GateInput) -> Decision | None:
    if i.lane is Lane.ROLLBACK:
        return Decision(Verdict.ADMIT, "ROLLBACK_LANE",
                        "Rollback to a previously healthy build is free", price_pct=0.0,
                        canary_profile=(100,))
    return None


def override_lane(i: GateInput) -> Decision | None:
    if i.lane is Lane.OVERRIDE:
        return Decision(Verdict.ADMIT, "OVERRIDE",
                        "Approved by a signed-in approver with a recorded reason; forced 5% canary",
                        price_pct=i.price_pct, canary_profile=CONSERVATIVE)
    return None


def integrity_check(i: GateInput) -> Decision | None:
    unknown = {Integrity.NO_DATA, Integrity.STALE}
    if not i.low_traffic_prices_normally:
        unknown.add(Integrity.LOW_TRAFFIC)
    if i.integrity not in unknown and i.remaining_pct is not None:
        return None
    if i.failure_mode == "fail_closed":
        return Decision(Verdict.BLOCK, "SLI_UNKNOWN",
                        f"SLI integrity is {i.integrity}; service is fail-closed")
    return Decision(Verdict.ADMIT_UNVERIFIED, "SLI_UNKNOWN",
                    f"SLI integrity is {i.integrity}; admitted under fail-open, on-call paged",
                    canary_profile=CONSERVATIVE)


def incident_fix_lane(i: GateInput) -> Decision | None:
    if i.lane is Lane.INCIDENT_FIX:
        return Decision(Verdict.ADMIT, "INCIDENT_FIX_LANE",
                        "Proven fix for the active incident; forced 5% canary",
                        price_pct=i.price_pct, canary_profile=CONSERVATIVE)
    return None


def hard_stop(i: GateInput) -> Decision | None:
    if i.integrity is Integrity.LOW_TRAFFIC:
        return None                         # short windows are not meaningful here
    if i.active_page:
        return Decision(Verdict.BLOCK, "ACTIVE_PAGE",
                        f"{i.service} has a firing page-level burn alert")
    return None


def priced_admission(i: GateInput) -> Decision:
    assert i.remaining_pct is not None      # integrity_check returned early otherwise
    afford = affordable_pct(i.remaining_pct, i.reserve_pct, i.spend_fraction)
    profile = CONSERVATIVE if i.integrity is Integrity.LOW_TRAFFIC else canary_profile(i.remaining_pct)
    if i.price_pct <= afford:
        return Decision(Verdict.ADMIT, "WITHIN_BUDGET",
                        f"Price {i.price_pct:.2f}% <= affordable {afford:.2f}%",
                        price_pct=i.price_pct, affordable_pct=afford, canary_profile=profile)
    return Decision(Verdict.BLOCK, "OVER_BUDGET",
                    f"Price {i.price_pct:.2f}% > affordable {afford:.2f}%",
                    price_pct=i.price_pct, affordable_pct=afford)


CHAIN: tuple[Rule, ...] = (rollback_lane, override_lane, integrity_check, incident_fix_lane, hard_stop)


def evaluate(i: GateInput) -> Decision:
    for rule in CHAIN:
        decision = rule(i)
        if decision is not None:
            return decision
    return priced_admission(i)
