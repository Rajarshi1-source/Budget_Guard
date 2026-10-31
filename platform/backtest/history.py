"""History cases for the backtest. No look-ahead by construction.

Since week 5 the gate worker stores the raw DeployFacts inside decisions.inputs["facts"] at
decision time, so a history case is just "the facts we had, plus what happened next".
There are no deploys from before BudgetGuard in this repository, so the reference's AS-OF bootstrap
queries are not carried over.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from budgetguard.records import facts_from_json, facts_to_json

from .replay import Case, Outcome

HISTORY_SQL = text("""
SELECT d.id, d.inputs -> 'facts' AS facts, d.verdict,
       dep.is_change_failure, dep.change_failure_budget_pct
FROM decisions d
LEFT JOIN deployments dep ON dep.decision_id = d.id
WHERE d.decided_at >= :since
  AND d.inputs ? 'facts'
ORDER BY d.decided_at
""")


def load_history(conn: Connection, since: datetime) -> list[Case]:
    cases = []
    for row in conn.execute(HISTORY_SQL, {"since": since}).mappings():
        blocked_in_reality = row["verdict"] == "block"
        unknown = blocked_in_reality or row["is_change_failure"] is None
        label = None if unknown else bool(row["is_change_failure"])
        cases.append(Case(facts_from_json(row["facts"]),
                          Outcome(label, float(row["change_failure_budget_pct"] or 0.0)), "history"))
    return cases


def write_jsonl(cases: Iterable[Case], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps({"facts": facts_to_json(c.facts),
                                 "was_change_failure": c.outcome.was_change_failure,
                                 "budget_cost_pct": c.outcome.budget_cost_pct}) + "\n")


def read_jsonl(path: str) -> list[Case]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            out.append(Case(facts_from_json(r["facts"]),
                            Outcome(r["was_change_failure"], r["budget_cost_pct"]), "history"))
    return out
