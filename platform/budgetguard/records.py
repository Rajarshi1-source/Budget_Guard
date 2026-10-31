"""Decision records (W5.3, D-8). The gate worker gathers raw DeployFacts (what was known when it
decided, nothing after), derives the GateInput from them with the policy, and stores both:

  decisions.inputs = {facts, gate_input, attribution, lane_evidence, github}
  decisions.inputs_hash = GateInput.digest()

Storing facts rather than only the GateInput lets the backtester (W7) re-price a deploy under a
candidate policy; storing the GateInput lets anyone recompute the verdict byte for byte.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal

from .gate import Decision, GateInput, Lane, evaluate
from .integrity import Integrity
from .policy import Policy
from .pricing import ChangeFeatures, price_pct


@dataclass(frozen=True, slots=True)
class DeployFacts:
    """Everything known when the gate decided, and nothing after (no look-ahead)."""
    case_id: str                          # the GitHub delivery id for live decisions
    service: str
    at: datetime
    integrity: Integrity
    remaining_all_pct: float | None       # from bad
    remaining_self_pct: float | None      # from self_bad
    page_firing: bool                     # any page alert on the service
    self_share_1h: float | None           # self_bad / bad over the last hour; None if no bad events
    lane: Lane | None                     # already proven by lanes.py
    features: ChangeFeatures
    prior_deploys: int                    # strictly before `at`
    prior_failures: int                   # strictly before `at`
    median_failure_cost_pct: float | None  # from failures strictly before `at`
    failure_mode: Literal["fail_open", "fail_closed"] = "fail_open"


def to_gate_input(f: DeployFacts, p: Policy) -> GateInput:
    origin = p.attribution == "charge_origin"
    remaining = f.remaining_self_pct if origin else f.remaining_all_pct
    page = f.page_firing and (not origin or (f.self_share_1h or 0.0) >= p.self_page_share)
    return GateInput(
        service=f.service,
        policy_version=p.version,
        integrity=f.integrity,
        remaining_pct=None if remaining is None else round(remaining, 4),
        active_page=page,
        lane=f.lane,
        price_pct=round(price_pct(f.features, f.prior_failures, f.prior_deploys,
                                  f.median_failure_cost_pct, p.pricing), 4),
        reserve_pct=p.reserve_pct,
        spend_fraction=p.spend_fraction,
        failure_mode=f.failure_mode,
        low_traffic_prices_normally=p.low_traffic_prices_normally,
    )


def facts_to_json(f: DeployFacts) -> dict[str, Any]:
    d = asdict(f)
    d["at"] = f.at.isoformat()
    d["integrity"] = str(f.integrity)
    d["lane"] = None if f.lane is None else str(f.lane)
    return d


def facts_from_json(d: dict[str, Any]) -> DeployFacts:
    return DeployFacts(
        case_id=d["case_id"], service=d["service"], at=datetime.fromisoformat(d["at"]),
        integrity=Integrity(d["integrity"]),
        remaining_all_pct=d["remaining_all_pct"], remaining_self_pct=d["remaining_self_pct"],
        page_firing=d["page_firing"], self_share_1h=d["self_share_1h"],
        lane=Lane(d["lane"]) if d["lane"] else None,
        features=ChangeFeatures(**d["features"]),
        prior_deploys=d["prior_deploys"], prior_failures=d["prior_failures"],
        median_failure_cost_pct=d["median_failure_cost_pct"], failure_mode=d["failure_mode"],
    )


def gate_input_to_json(i: GateInput) -> dict[str, Any]:
    d = asdict(i)
    d["integrity"] = str(i.integrity)
    d["lane"] = None if i.lane is None else str(i.lane)
    return d


def gate_input_from_json(d: dict[str, Any]) -> GateInput:
    return GateInput(**{**d, "integrity": Integrity(d["integrity"]),
                        "lane": Lane(d["lane"]) if d["lane"] else None})


def build_inputs(facts: DeployFacts, gate_input: GateInput, policy: Policy,
                 lane_evidence: dict[str, Any], github: dict[str, Any]) -> dict[str, Any]:
    """The decisions.inputs document. `github` holds only identifiers (repo, installation id,
    environment, callback URL, run id): never a token, so inputs are safe to show and replay."""
    return {"facts": facts_to_json(facts), "gate_input": gate_input_to_json(gate_input),
            "attribution": policy.attribution, "lane_evidence": lane_evidence, "github": github}


@dataclass(frozen=True, slots=True)
class Replay:
    decision: Decision
    inputs_hash: str
    matches: bool


def replay(inputs: dict[str, Any], stored_hash: str, stored_verdict: str, stored_reason: str) -> Replay:
    """Recompute from the stored GateInput with the current code. Writes nothing (W5.7)."""
    gi = gate_input_from_json(inputs["gate_input"])
    d = evaluate(gi)
    h = gi.digest()
    same = h == stored_hash and str(d.verdict) == stored_verdict and d.reason_code == stored_reason
    return Replay(d, h, same)
