"""Backtest CLI used by policy-backtest.yml.

  python -m backtest.cli score   --policy policy/freeze-policy.yaml --version $SHA \
                                [--history h.jsonl] --out s.json
  python -m backtest.cli compare --base base.json --cand cand.json --report report.md   # exit 1 on failure
  python -m backtest.cli publish --version $SHA --report report.md    # on merge: dashboard /policy shows it

`score` runs in two checkouts (main and the PR), so a change to gate.py or pricing.py is
measured with each side's own code, not just each side's policy file.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .history import read_jsonl
from .replay import Policy, Score, gate_ci, replay, report_markdown, score
from .synthetic import population, scenario_cases


def cmd_score(a: argparse.Namespace) -> int:
    cases = scenario_cases() + population(seed=a.seed)
    if a.history and Path(a.history).exists():
        cases += read_jsonl(a.history)
    policy = Policy.from_yaml(Path(a.policy), a.version)
    decisions = replay(cases, policy)
    s = score(cases, decisions, policy)
    out = {"version": a.version, "score": asdict(s),
           "decisions": {k: {"verdict": str(d.verdict), "reason_code": d.reason_code}
                         for k, d in decisions.items()},
           "outcomes": {c.facts.case_id: [c.scenario, c.outcome.was_change_failure] for c in cases}}
    Path(a.out).write_text(json.dumps(out), encoding="utf-8")
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    base = json.loads(Path(a.base).read_text(encoding="utf-8"))
    cand = json.loads(Path(a.cand).read_text(encoding="utf-8"))
    sb = Score(**{**base["score"], "violations": tuple(base["score"]["violations"]),
                  "anchor_moves": tuple(base["score"]["anchor_moves"])})
    sc = Score(**{**cand["score"], "violations": tuple(cand["score"]["violations"]),
                  "anchor_moves": tuple(cand["score"]["anchor_moves"])})
    failures, warnings = gate_ci(sc, sb)
    label = {True: "change failure", False: "healthy", None: "unknown"}
    diff = [{"case": k, "scenario": cand["outcomes"][k][0], "from": base["decisions"][k]["verdict"],
             "to": v["verdict"], "outcome": label[cand["outcomes"][k][1]]}
            for k, v in cand["decisions"].items()
            if k in base["decisions"] and base["decisions"][k]["verdict"] != v["verdict"]]
    Path(a.report).write_text(report_markdown(sb, sc, failures, warnings, diff,
                                              base["version"], cand["version"]), encoding="utf-8")
    return 1 if failures else 0


def cmd_publish(a: argparse.Namespace) -> int:
    """Stores the merged policy's report in policy_versions.backtest_report (BG_POLICY_DSN: a role
    with INSERT/UPDATE on that one table)."""
    import os

    from sqlalchemy import create_engine, text
    report = Path(a.report).read_text(encoding="utf-8")
    engine = create_engine(os.environ["BG_POLICY_DSN"])
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO policy_versions (version, backtest_report)
            VALUES (:v, jsonb_build_object('markdown', CAST(:r AS text)))
            ON CONFLICT (version) DO UPDATE SET backtest_report = EXCLUDED.backtest_report
        """), {"v": a.version, "r": report})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--policy", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--history")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--base", required=True)
    c.add_argument("--cand", required=True)
    c.add_argument("--report", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--version", required=True)
    p.add_argument("--report", required=True)
    a = ap.parse_args()
    return {"score": cmd_score, "compare": cmd_compare, "publish": cmd_publish}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
