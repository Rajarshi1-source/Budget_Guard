from dataclasses import replace
from pathlib import Path

from backtest.replay import (
    Case,
    Outcome,
    Policy,
    gate_ci,
    replay,
    report_markdown,
    score,
    to_gate_input,
    verdict_diff,
)
from backtest.synthetic import BIG_MIGRATION, _facts, population, scenario_cases
from budgetguard.gate import Verdict
from budgetguard.pricing import PricingPolicy
from budgetguard.records import DeployFacts

POLICY = Policy.from_yaml(Path(__file__).resolve().parents[2] / "policy/freeze-policy.yaml", "a" * 40)


def test_policy_yaml_loads_and_matches_defaults() -> None:
    assert POLICY.pricing == PricingPolicy()
    assert POLICY.attribution == "charge_origin"


def test_backtest_replays_the_gates_own_records() -> None:
    assert _facts("x").__class__ is DeployFacts          # one DeployFacts: the gate's (budgetguard.records)


def test_worked_example_replays() -> None:
    f = _facts("x", remaining_all=7, remaining_self=7, features=BIG_MIGRATION)
    gi = to_gate_input(f, POLICY)
    assert round(gi.price_pct, 2) == 1.26
    d = replay([Case(f, Outcome(True, 3.0), "synthetic")], POLICY)["x"]
    assert d.verdict is Verdict.BLOCK and d.affordable_pct is not None and round(d.affordable_pct, 2) == 1.0


def test_pricing_weight_change_changes_replay() -> None:
    """The Rev 2 pitfall: a stored GateInput would replay identically after a weight change."""
    f = _facts("x", remaining_all=7, remaining_self=7, features=BIG_MIGRATION)
    cases = [Case(f, Outcome(True, 3.0), "synthetic")]
    softer = replace(POLICY, version="b" * 40, pricing=replace(POLICY.pricing, w_migration=1.2))
    assert replay(cases, POLICY)["x"].verdict is Verdict.BLOCK
    assert replay(cases, softer)["x"].verdict is Verdict.ADMIT


def test_scenarios_pass_under_recommended_policy() -> None:
    cases = scenario_cases()
    s = score(cases, replay(cases, POLICY), POLICY)
    assert s.violations == ()
    assert s.rollback_admit_rate == 1.0


def test_charge_victim_policy_fails_d3() -> None:
    cases = scenario_cases()
    victim = replace(POLICY, attribution="charge_victim")
    s = score(cases, replay(cases, victim), victim)
    assert any("dep-outage-victim" in v for v in s.violations)
    failures, _ = gate_ci(s, s)
    assert any("invariant violated" in f for f in failures)


def test_rev2_low_traffic_does_not_block_fail_open_but_does_block_fail_closed() -> None:
    cases = scenario_cases()
    rev2 = replace(POLICY, low_traffic_prices_normally=False)
    d = replay(cases, rev2)
    assert d["low-traffic"].verdict is Verdict.ADMIT_UNVERIFIED     # pages on-call at night
    closed = [replace(c, facts=replace(c.facts, failure_mode="fail_closed"))
              for c in cases if c.facts.case_id == "low-traffic"]
    assert score(closed, replay(closed, rev2), rev2).violations              # fail-closed: blocked


def test_unlabelled_cases_are_excluded() -> None:
    f = _facts("u", page=True, self_share=1.0)
    cases = [Case(f, Outcome(None), "history")]
    s = score(cases, replay(cases, POLICY), POLICY)
    assert s.unlabelled == 1 and s.labelled == 0 and s.blocked_healthy == 0


def test_population_is_deterministic_and_has_no_lookahead() -> None:
    a, b = population(seed=3), population(seed=3)
    assert [c.facts for c in a] == [c.facts for c in b]
    by_service: dict[str, Case] = {}
    for c in a:
        prev = by_service.get(c.facts.service)
        if prev:
            assert c.facts.prior_deploys == prev.facts.prior_deploys + 1
            failed = int(bool(prev.outcome.was_change_failure))
            assert c.facts.prior_failures == prev.facts.prior_failures + failed
            assert c.facts.at > prev.facts.at
        by_service[c.facts.service] = c


def test_gate_ci_detects_regressions_and_report_renders() -> None:
    cases = scenario_cases() + population()
    base = replay(cases, POLICY)
    loose = replace(POLICY, version="c" * 40, spend_fraction=1.0, reserve_pct=0.0)
    cand = replay(cases, loose)
    sb, sc = score(cases, base, POLICY), score(cases, cand, loose)
    failures, warnings = gate_ci(sc, sb)
    assert sb.change_failures >= 20
    assert any("slow-burn-big" in w for w in warnings)          # anchor moved: warning, not failure
    md = report_markdown(sb, sc, failures, warnings, verdict_diff(cases, base, cand), POLICY.version,
                         loose.version)
    assert "Policy backtest" in md


def test_rollback_blocked_fails_ci() -> None:
    cases = scenario_cases()
    s = score(cases, replay(cases, POLICY), POLICY)
    bad = replace(s, rollback_admit_rate=0.9)
    assert "a rollback was blocked" in gate_ci(bad, s)[0]


def test_lowering_the_reserve_to_3pct_is_blocked() -> None:
    """Plan W7 exit / Rev 2 demo step 7: a PR that lowers the reserve to 3% gets a table and fails."""
    cases = scenario_cases() + population()
    lower = replace(POLICY, version="d" * 40, reserve_pct=3.0)
    sb = score(cases, replay(cases, POLICY), POLICY)
    sc = score(cases, replay(cases, lower), lower)
    failures, _ = gate_ci(sc, sb)
    assert failures, (sb, sc)
    assert any("harm recall fell" in f or "recall fell" in f for f in failures)
