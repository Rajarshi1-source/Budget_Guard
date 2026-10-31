import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from budgetguard.gate import CONSERVATIVE, GateInput, Lane, Verdict, canary_profile, evaluate
from budgetguard.integrity import Integrity
from budgetguard.policy import Policy
from budgetguard.pricing import ChangeFeatures, PricingPolicy, price_pct, risk_multiplier
from budgetguard.records import (
    DeployFacts,
    facts_from_json,
    facts_to_json,
    gate_input_from_json,
    gate_input_to_json,
    replay,
    to_gate_input,
)
from budgetguard.rulegen.spec import load_dir

ROOT = Path(__file__).resolve().parents[2]
P = PricingPolicy()
SMALL = ChangeFeatures(40, False, False, False)
BIG_MIGRATION = ChangeFeatures(900, True, False, False)


def gi(**kw: object) -> GateInput:
    base: dict[str, object] = dict(service="orders-service", policy_version="v1", integrity=Integrity.OK,
                                   remaining_pct=18.0, active_page=False, lane=None, price_pct=0.42,
                                   reserve_pct=5.0, spend_fraction=0.5, failure_mode="fail_open")
    return GateInput(**{**base, **kw})  # type: ignore[arg-type]


# ---------------------------------------------------------------- Rev 2 §D worked example
# orders-service, n = 30, f = 3, prior Beta(2,18) -> CFR 5/50 = 10%; median failure cost 4.2%.
@pytest.mark.parametrize(("remaining", "features", "lane", "price", "afford", "verdict"), [
    (18.0, SMALL, None, 0.42, 6.5, Verdict.ADMIT),
    (7.0, SMALL, None, 0.42, 1.0, Verdict.ADMIT),                    # Rev 1 froze here
    (7.0, BIG_MIGRATION, None, 1.26, 1.0, Verdict.BLOCK),
    (7.0, BIG_MIGRATION, Lane.ROLLBACK, 0.0, None, Verdict.ADMIT),   # proven rollback is free
])
def test_worked_example(remaining: float, features: ChangeFeatures, lane: Lane | None, price: float,
                        afford: float | None, verdict: Verdict) -> None:
    p = round(price_pct(features, failures=3, deploys=30, median_failure_cost_pct=4.2, p=P), 4)
    d = evaluate(gi(remaining_pct=remaining, price_pct=p, lane=lane))
    assert d.verdict is verdict
    assert d.price_pct == pytest.approx(price)
    assert d.affordable_pct == (None if afford is None else pytest.approx(afford))


def test_900_line_migration_multiplier_is_size_max_times_migration_weight() -> None:
    m = risk_multiplier(BIG_MIGRATION, P)
    assert m == pytest.approx(P.size_max * P.w_migration) == pytest.approx(3.0)


def test_no_history_prices_at_the_prior() -> None:
    assert price_pct(SMALL, 0, 0, None, P) == pytest.approx(0.10 * 1.0 * 5.0)
    assert price_pct(ChangeFeatures.unknown(), 0, 0, None, P) == pytest.approx(0.10 * 1.5 * 5.0)


# ---------------------------------------------------------------- chain order and lanes
def test_rollback_beats_unknown_sli_on_a_fail_closed_service() -> None:
    d = evaluate(gi(integrity=Integrity.STALE, remaining_pct=None, failure_mode="fail_closed",
                    active_page=True, lane=Lane.ROLLBACK))
    assert (d.verdict, d.reason_code, d.canary_profile) == (Verdict.ADMIT, "ROLLBACK_LANE", (100,))


def test_override_beats_no_data_fail_closed_and_a_page() -> None:
    d = evaluate(gi(integrity=Integrity.NO_DATA, remaining_pct=None, failure_mode="fail_closed",
                    active_page=True, lane=Lane.OVERRIDE, price_pct=9.0))
    assert (d.verdict, d.reason_code, d.canary_profile) == (Verdict.ADMIT, "OVERRIDE", CONSERVATIVE)


def test_unknown_sli_follows_the_failure_mode() -> None:
    assert evaluate(gi(integrity=Integrity.STALE, failure_mode="fail_closed")).verdict is Verdict.BLOCK
    d = evaluate(gi(integrity=Integrity.NO_DATA))
    assert (d.verdict, d.canary_profile) == (Verdict.ADMIT_UNVERIFIED, CONSERVATIVE)
    assert evaluate(gi(remaining_pct=None)).reason_code == "SLI_UNKNOWN"     # no buckets = unknown


def test_active_page_blocks_but_an_incident_fix_passes() -> None:
    assert evaluate(gi(active_page=True)).reason_code == "ACTIVE_PAGE"
    d = evaluate(gi(active_page=True, lane=Lane.INCIDENT_FIX))
    assert (d.verdict, d.canary_profile) == (Verdict.ADMIT, CONSERVATIVE)


def test_low_traffic_prices_normally_with_a_conservative_canary() -> None:
    d = evaluate(gi(integrity=Integrity.LOW_TRAFFIC, remaining_pct=80.0, active_page=True))
    assert (d.verdict, d.reason_code, d.canary_profile) == (Verdict.ADMIT, "WITHIN_BUDGET", CONSERVATIVE)


def test_low_traffic_flag_off_is_rev2_behaviour() -> None:
    d = evaluate(gi(integrity=Integrity.LOW_TRAFFIC, remaining_pct=80.0, low_traffic_prices_normally=False))
    assert (d.verdict, d.reason_code) == (Verdict.ADMIT_UNVERIFIED, "SLI_UNKNOWN")


@pytest.mark.parametrize(("remaining", "profile"), [
    (None, CONSERVATIVE), (19.9, CONSERVATIVE), (20.0, (10, 50, 100)), (49.9, (10, 50, 100)),
    (50.0, (25, 100))])
def test_canary_profile_bands(remaining: float | None, profile: tuple[int, ...]) -> None:
    assert canary_profile(remaining) == profile


def test_overspent_budget_affords_nothing() -> None:
    d = evaluate(gi(remaining_pct=-40.0, price_pct=0.01))
    assert (d.verdict, d.affordable_pct) == (Verdict.BLOCK, 0.0)


# ---------------------------------------------------------------- determinism (D-8)
def test_evaluate_is_deterministic() -> None:
    i = gi(remaining_pct=7.0, price_pct=1.26)
    assert evaluate(i) == evaluate(i)
    assert i.digest() == gi(remaining_pct=7.0, price_pct=1.26).digest()
    assert i.digest() != gi(remaining_pct=7.0, price_pct=1.25).digest()


def test_digest_is_stable_across_processes() -> None:
    code = ("from budgetguard.gate import GateInput, Lane; from budgetguard.integrity import Integrity;"
            "print(GateInput('orders-service','v1',Integrity.OK,7.0,False,Lane.ROLLBACK,1.26,5.0,0.5,"
            "'fail_open').digest())")
    out = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                          env={"PYTHONHASHSEED": seed, "SYSTEMROOT": "C:\\Windows", "PATH": ""}).stdout
           for seed in ("1", "2")}
    assert out == {gi(remaining_pct=7.0, price_pct=1.26, lane=Lane.ROLLBACK).digest() + "\n"}


# ---------------------------------------------------------------- records and policy
def facts(**kw: object) -> DeployFacts:
    base: dict[str, object] = dict(case_id="d-1", service="orders-service",
                                   at=datetime(2026, 10, 31, tzinfo=UTC),
                                   integrity=Integrity.OK, remaining_all_pct=2.0, remaining_self_pct=60.0,
                                   page_firing=True, self_share_1h=0.1, lane=None, features=SMALL,
                                   prior_deploys=30, prior_failures=3, median_failure_cost_pct=4.2)
    return DeployFacts(**{**base, **kw})  # type: ignore[arg-type]


def test_charge_origin_does_not_freeze_the_victim_of_a_dependency_outage() -> None:
    # orders pages and its all-errors budget is gone, but 90% of the bad events are auth's (D-3)
    origin = evaluate(to_gate_input(facts(), Policy("v1")))
    victim = evaluate(to_gate_input(facts(), Policy("v1", attribution="charge_victim")))
    assert origin.verdict is Verdict.ADMIT
    assert victim.verdict is Verdict.BLOCK and victim.reason_code == "ACTIVE_PAGE"


def test_self_inflicted_page_still_freezes_under_charge_origin() -> None:
    d = evaluate(to_gate_input(facts(self_share_1h=0.8), Policy("v1")))
    assert d.reason_code == "ACTIVE_PAGE"


def test_facts_and_gate_input_round_trip_through_json() -> None:
    f = facts(lane=Lane.ROLLBACK, integrity=Integrity.LOW_TRAFFIC)
    assert facts_from_json(facts_to_json(f)) == f
    g = to_gate_input(f, Policy("v1"))
    assert gate_input_from_json(gate_input_to_json(g)) == g


def test_replay_matches_and_detects_tampering() -> None:
    g = to_gate_input(facts(self_share_1h=0.8), Policy("v1"))
    d = evaluate(g)
    inputs = {"gate_input": gate_input_to_json(g)}
    assert replay(inputs, g.digest(), str(d.verdict), d.reason_code).matches
    tampered = {"gate_input": {**gate_input_to_json(g), "active_page": False}}
    r = replay(tampered, g.digest(), str(d.verdict), d.reason_code)
    assert not r.matches and r.inputs_hash != g.digest()


def test_policy_file_loads_and_rejects_typos() -> None:
    p = Policy.from_yaml(ROOT / "policy" / "freeze-policy.yaml", "abc")
    assert (p.version, p.attribution) == ("abc", "charge_origin")
    assert p.pricing.size_tiers == ((200, 1.0), (500, 1.25))
    assert p.pricing == PricingPolicy()
    with pytest.raises(ValueError, match="unknown policy keys"):
        Policy.from_dict({"reserve_pc": 5}, "x")
    with pytest.raises(ValueError, match="attribution"):
        Policy.from_dict({"attribution": "charge_everyone"}, "x")


def test_spec_gate_blocks_agree_with_the_policy() -> None:
    """The gate reads reserve and spend fraction from the backtested policy; the cards read the
    spec's gate block. They must not drift apart."""
    p = Policy.from_yaml(ROOT / "policy" / "freeze-policy.yaml", "x")
    for sl in load_dir(ROOT / "slo"):
        assert (sl.spec.gate.reserve_pct, sl.spec.gate.spend_fraction) == (p.reserve_pct, p.spend_fraction), \
            sl.metadata.service
