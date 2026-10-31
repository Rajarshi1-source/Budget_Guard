"""Policy backtest (D-6): replay deploys through a candidate policy and score it.

Differences from Rev 2 §10.3, each deliberate:
  * A Case stores the *facts known at decision time*, not a finished GateInput. Price is
    recomputed from the candidate policy, so a PR that changes a pricing weight actually
    changes the replay (with a stored GateInput it would replay identically).
  * Historical deploys that were blocked in reality have no observed outcome; they are
    counted as `unlabelled` and excluded from precision and recall.
  * Synthetic cases carry hard expectations (D-3, D-4, rollback lane). Any violation fails CI.

DeployFacts, to_gate_input and Policy are the gate's own (budgetguard.records, budgetguard.policy),
so the backtest replays exactly the code that decides real deploys.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from budgetguard.gate import Decision, Lane, Verdict, evaluate
from budgetguard.policy import Policy
from budgetguard.records import DeployFacts, to_gate_input

__all__ = ["Case", "DeployFacts", "Outcome", "Policy", "Score", "gate_ci", "replay", "report_markdown",
           "score", "to_gate_input", "verdict_diff"]


@dataclass(frozen=True, slots=True)
class Outcome:
    was_change_failure: bool | None       # None: blocked in reality, outcome never observed
    budget_cost_pct: float = 0.0


@dataclass(frozen=True, slots=True)
class Case:
    facts: DeployFacts
    outcome: Outcome
    source: Literal["history", "synthetic"]
    scenario: str = "history"
    must_not: frozenset[Verdict] = frozenset()      # hard expectation for synthetic invariants
    must_be: frozenset[Verdict] = frozenset()
    strength: Literal["invariant", "anchor"] = "invariant"   # anchor = worked example; may move with policy


@dataclass(frozen=True, slots=True)
class Score:
    cases: int
    labelled: int
    unlabelled: int
    change_failures: int
    precision: float
    recall: float
    harm_recall: float                    # blocked share of failures that pushed the budget below reserve
    prevented_burn_pct: float
    blocked_healthy: int
    rollback_admit_rate: float
    violations: tuple[str, ...]           # invariant cases that got a forbidden verdict -> CI fails
    anchor_moves: tuple[str, ...] = ()    # worked-example cases whose verdict moved -> PR comment warns


def replay(cases: list[Case], p: Policy) -> dict[str, Decision]:
    return {c.facts.case_id: evaluate(to_gate_input(c.facts, p)) for c in cases}


def _remaining(f: DeployFacts, p: Policy) -> float | None:
    return f.remaining_self_pct if p.attribution == "charge_origin" else f.remaining_all_pct


def score(cases: list[Case], decisions: dict[str, Decision], p: Policy) -> Score:
    """Plain recall is low by design: the gate only blocks when the budget cannot afford a
    change, so most failures happen while it can. harm_recall asks the question the gate is
    for: of the failures that took the budget below the reserve, how many were stopped?"""
    tp = fp = fn = 0
    harmful = harmful_blocked = 0
    prevented = 0.0
    rollbacks = admitted_rollbacks = 0
    unlabelled = failures = 0
    violations: list[str] = []
    anchors: list[str] = []
    for c in cases:
        d = decisions[c.facts.case_id]
        blocked = d.verdict is Verdict.BLOCK
        if c.facts.lane is Lane.ROLLBACK:
            rollbacks += 1
            admitted_rollbacks += not blocked
        sink = violations if c.strength == "invariant" else anchors
        if c.must_not and d.verdict in c.must_not:
            sink.append(f"{c.facts.case_id} [{c.scenario}]: got {d.verdict} ({d.reason_code})")
        if c.must_be and d.verdict not in c.must_be:
            sink.append(f"{c.facts.case_id} [{c.scenario}]: got {d.verdict}, "
                        f"expected {' or '.join(sorted(str(v) for v in c.must_be))}")
        label = c.outcome.was_change_failure
        if label is None:
            unlabelled += 1
            continue
        failures += label
        rem = _remaining(c.facts, p)
        if label and rem is not None and rem - c.outcome.budget_cost_pct < p.reserve_pct:
            harmful += 1
            harmful_blocked += blocked
        if blocked and label:
            tp += 1
            prevented += c.outcome.budget_cost_pct
        elif blocked:
            fp += 1
        elif label:
            fn += 1
    return Score(
        cases=len(cases), labelled=len(cases) - unlabelled, unlabelled=unlabelled,
        change_failures=failures,
        precision=tp / (tp + fp) if tp + fp else 1.0,
        recall=tp / (tp + fn) if tp + fn else 1.0,
        harm_recall=harmful_blocked / harmful if harmful else 1.0,
        prevented_burn_pct=round(prevented, 4),
        blocked_healthy=fp,
        rollback_admit_rate=admitted_rollbacks / rollbacks if rollbacks else 1.0,
        violations=tuple(violations),
        anchor_moves=tuple(anchors),
    )


MIN_FAILURES_FOR_EVIDENCE = 20


def gate_ci(candidate: Score, baseline: Score, tolerance_pp: float = 2.0) -> tuple[list[str], list[str]]:
    """Returns (failures, warnings). Empty failures means the policy PR may merge."""
    failures: list[str] = []
    warnings: list[str] = []
    if candidate.rollback_admit_rate < 1.0:
        failures.append("a rollback was blocked")
    failures += [f"invariant violated: {v}" for v in candidate.violations]
    for name, label in (("precision", "precision"), ("recall", "recall"), ("harm_recall", "harm recall")):
        drop = 100 * (getattr(baseline, name) - getattr(candidate, name))
        if drop > tolerance_pp:
            failures.append(f"{label} fell {drop:.1f}pp")
    if candidate.blocked_healthy > baseline.blocked_healthy * 1.2 + 1:
        failures.append("healthy deploys blocked rose more than 20%")
    warnings += [f"worked example moved (update the docs if intended): {a}" for a in candidate.anchor_moves]
    if candidate.change_failures < MIN_FAILURES_FOR_EVIDENCE:
        warnings.append(f"only {candidate.change_failures} labelled change failures; "
                        f"precision and recall deltas are weak evidence below {MIN_FAILURES_FOR_EVIDENCE}")
    return failures, warnings


def verdict_diff(cases: list[Case], base: dict[str, Decision],
                 cand: dict[str, Decision]) -> list[dict[str, Any]]:
    rows = []
    for c in cases:
        b, n = base[c.facts.case_id], cand[c.facts.case_id]
        if b.verdict != n.verdict:
            rows.append({"case": c.facts.case_id, "scenario": c.scenario,
                         "from": str(b.verdict), "to": str(n.verdict),
                         "outcome": {True: "change failure", False: "healthy",
                                     None: "unknown"}[c.outcome.was_change_failure]})
    return rows


def report_markdown(base: Score, cand: Score, failures: list[str], warnings: list[str],
                    diff: list[dict[str, Any]], base_version: str, cand_version: str) -> str:
    def row(name: str, a: float, b: float, fmt: str = "{:.3f}") -> str:
        return f"| {name} | {fmt.format(a)} | {fmt.format(b)} | {fmt.format(b - a)} |"
    lines = [
        f"### Policy backtest: `{base_version[:7]}` → `{cand_version[:7]}`",
        "",
        "**PASS**" if not failures else "**FAIL**",
        "",
        "| Metric | main | this PR | Δ |",
        "| --- | --- | --- | --- |",
        row("Precision (blocked deploys that would have failed)", base.precision, cand.precision),
        row("Recall (failures the policy would have blocked)", base.recall, cand.recall),
        row("Harm recall (failures that broke the reserve, blocked)", base.harm_recall, cand.harm_recall),
        row("Healthy deploys blocked", base.blocked_healthy, cand.blocked_healthy, "{:.0f}"),
        row("Budget burn prevented (%)", base.prevented_burn_pct, cand.prevented_burn_pct, "{:.2f}"),
        row("Rollback admit rate", base.rollback_admit_rate, cand.rollback_admit_rate),
        "",
        f"Cases: {cand.cases} ({cand.labelled} labelled, {cand.unlabelled} with unknown outcome, "
        f"e.g. blocked in reality, excluded); labelled change failures: {cand.change_failures}.",
    ]
    if failures:
        lines += ["", "**Failures**", *[f"- {f}" for f in failures]]
    if warnings:
        lines += ["", "**Warnings**", *[f"- {w}" for w in warnings]]
    if diff:
        lines += ["", f"<details><summary>{len(diff)} verdicts changed</summary>", "",
                  "| Case | Scenario | main | this PR | Real outcome |", "| --- | --- | --- | --- | --- |",
                  *[f"| {d['case']} | {d['scenario']} | {d['from']} | {d['to']} | {d['outcome']} |"
                    for d in diff[:50]],
                  "", "</details>"]
    return "\n".join(lines)
