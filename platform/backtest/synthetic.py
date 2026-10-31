"""Synthetic incident library (Rev 2 §10.4) as backtest cases. Seeded: same seed, same cases.

Two parts:
  * scenario_cases(): one small, hand-shaped case set per scenario with HARD expectations.
    These are invariants (D-3, D-4, rollback lane); a policy that violates one cannot merge.
  * population(): a seeded stream of ordinary deploys whose failure labels are drawn from a
    "true" risk model that is deliberately NOT identical to the policy's weights, so the
    backtest measures how well a policy approximates reality instead of grading itself.
"""
from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

from budgetguard.gate import Lane, Verdict
from budgetguard.integrity import Integrity
from budgetguard.pricing import ChangeFeatures

from .replay import Case, DeployFacts, Outcome

T0 = datetime(2026, 7, 1, tzinfo=UTC)
SERVICES = ("auth-service", "orders-service", "inventory-service", "notifications-service")
SMALL = ChangeFeatures(lines_changed=40, touches_migration=False, config_only=False, dependency_bump=False)
BIG_MIGRATION = ChangeFeatures(lines_changed=900, touches_migration=True, config_only=False,
                               dependency_bump=False)


def _facts(case_id: str, service: str = "orders-service", *, at: datetime = T0,
           integrity: Integrity = Integrity.OK, remaining_all: float | None = 60.0,
           remaining_self: float | None = 60.0, page: bool = False, self_share: float | None = None,
           lane: Lane | None = None, features: ChangeFeatures = SMALL,
           deploys: int = 30, failures: int = 3, cost: float | None = 4.2,
           failure_mode: str = "fail_open") -> DeployFacts:
    return DeployFacts(case_id, service, at, integrity, remaining_all, remaining_self, page, self_share,
                       lane, features, deploys, failures, cost, failure_mode)  # type: ignore[arg-type]


def scenario_cases() -> list[Case]:
    B, A, U = Verdict.BLOCK, Verdict.ADMIT, Verdict.ADMIT_UNVERIFIED
    return [
        # Fast burn: 20x self-inflicted for 15 min after a deploy. Next feature blocked,
        # rollback and proven fix admitted.
        Case(_facts("fast-burn-feature", page=True, self_share=0.95, remaining_all=55, remaining_self=55),
             Outcome(True, 6.0), "synthetic", "fast_burn", must_be=frozenset({B})),
        Case(_facts("fast-burn-rollback", page=True, self_share=0.95, lane=Lane.ROLLBACK),
             Outcome(False), "synthetic", "fast_burn", must_be=frozenset({A})),
        Case(_facts("fast-burn-fix", page=True, self_share=0.95, lane=Lane.INCIDENT_FIX),
             Outcome(False), "synthetic", "fast_burn", must_be=frozenset({A})),
        # Slow burn: budget nearly gone; a big risky change is priced out, a small one is not.
        # These are the Rev 2 §D worked example: "anchors" that may legitimately move with policy.
        Case(_facts("slow-burn-big", remaining_all=7, remaining_self=7, features=BIG_MIGRATION),
             Outcome(True, 3.5), "synthetic", "slow_burn", must_be=frozenset({B}), strength="anchor"),
        Case(_facts("slow-burn-small", remaining_all=7, remaining_self=7),
             Outcome(False), "synthetic", "slow_burn", must_be=frozenset({A}), strength="anchor"),
        # Dependency outage: auth fails, orders' errors are 95% dependency-caused.
        # D-3: orders (the victim) must not be frozen; auth (the culprit) must be.
        Case(_facts("dep-outage-victim", page=True, self_share=0.05, remaining_all=3, remaining_self=70),
             Outcome(False), "synthetic", "dependency_outage", must_not=frozenset({B})),
        Case(_facts("dep-outage-culprit", "auth-service", page=True, self_share=1.0,
                    remaining_all=20, remaining_self=20),
             Outcome(True, 5.0), "synthetic", "dependency_outage", must_be=frozenset({B})),
        # Dead service / scrape loss: never HEALTHY-admitted (D-4).
        Case(_facts("dead-service-open", integrity=Integrity.NO_DATA, remaining_all=None,
                    remaining_self=None),
             Outcome(None), "synthetic", "dead_service", must_be=frozenset({U})),
        Case(_facts("dead-service-closed", "auth-service", integrity=Integrity.NO_DATA, remaining_all=None,
                    remaining_self=None, failure_mode="fail_closed"),
             Outcome(None), "synthetic", "dead_service", must_be=frozenset({B})),
        Case(_facts("scrape-loss", integrity=Integrity.STALE),
             Outcome(None), "synthetic", "scrape_loss", must_not=frozenset({A})),
        Case(_facts("dead-service-rollback", integrity=Integrity.NO_DATA, remaining_all=None,
                    remaining_self=None, failure_mode="fail_closed", lane=Lane.ROLLBACK),
             Outcome(False), "synthetic", "dead_service", must_be=frozenset({A})),
        # Override: break glass works even when the SLI is unknown, the service is fail-closed
        # and a page is firing; it is still a recorded, replayable decision.
        Case(_facts("override-break-glass", "auth-service", integrity=Integrity.NO_DATA, remaining_all=None,
                    remaining_self=None, page=True, self_share=1.0, lane=Lane.OVERRIDE,
                    failure_mode="fail_closed"),
             Outcome(None), "synthetic", "override", must_be=frozenset({A})),
        # Flapping: two-window alerts stay quiet, so nothing is firing; a small change passes.
        Case(_facts("flapping", remaining_all=55, remaining_self=55),
             Outcome(False), "synthetic", "flapping", must_be=frozenset({A}), strength="anchor"),
        # Low traffic at night: the floor suppresses pages; the deploy must not be blocked.
        Case(_facts("low-traffic", "inventory-service", integrity=Integrity.LOW_TRAFFIC,
                    remaining_all=80, remaining_self=80),
             Outcome(False), "synthetic", "low_traffic", must_not=frozenset({B})),
    ]


# "True" risk model used only to draw labels. Its weights differ from PricingPolicy on purpose.
TRUE_BASE_CFR = {"auth-service": 0.06, "orders-service": 0.10,
                 "inventory-service": 0.08, "notifications-service": 0.05}


def _true_failure_prob(service: str, f: ChangeFeatures, remaining: float) -> float:
    p = TRUE_BASE_CFR[service]
    p *= 1.0 + min(f.lines_changed, 2000) / 800          # size matters smoothly, not in tiers
    if f.touches_migration:
        p *= 2.4
    if f.config_only:
        p *= 0.4
    if f.dependency_bump:
        p *= 1.3
    if remaining < 15:
        p *= 1.3                                          # teams under pressure ship worse changes
    return min(p, 0.9)


# Budget phases each service cycles through, so the population visits every budget regime the
# gate must handle: plenty, scarce, and overspent. (A free random walk mostly sits near 100%,
# which makes every policy look identical.)
PHASES = ((70.0, 100.0), (15.0, 45.0), (-10.0, 12.0))


def population(seed: int = 7, per_service: int = 150, phase_len: int = 25) -> list[Case]:
    rng = random.Random(seed)
    cases: list[Case] = []
    for service in SERVICES:
        failures = 0
        costs: list[float] = []
        at = T0
        for n in range(per_service):
            at += timedelta(hours=rng.uniform(2, 14))
            lo, hi = PHASES[(n // phase_len) % len(PHASES)]
            remaining = round(rng.uniform(lo, hi), 2)
            config_only = rng.random() < 0.2
            f = ChangeFeatures(
                lines_changed=5 if config_only else int(math.exp(rng.gauss(4.2, 1.1))),
                touches_migration=(not config_only) and rng.random() < 0.1,
                config_only=config_only,
                dependency_bump=(not config_only) and rng.random() < 0.1,
            )
            median_cost = sorted(costs)[len(costs) // 2] if costs else None
            facts = _facts(f"{service}-{n:03d}", service, at=at, remaining_all=remaining,
                           remaining_self=remaining, features=f, deploys=n,
                           failures=failures, cost=median_cost)
            failed = rng.random() < _true_failure_prob(service, f, remaining)
            cost = round(rng.gammavariate(2.0, 2.1), 3) if failed else 0.0
            cases.append(Case(facts, Outcome(failed, cost), "synthetic", "population"))
            failures += failed
            if failed:
                costs.append(cost)
    return cases
