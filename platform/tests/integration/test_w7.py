"""W7 integration: the scripts CI runs against PostgreSQL. Skipped without BG_TEST_DATABASE_URL.

  evals.record_run         one eval_runs row per (suite, model, prompt_sha) run
  backtest.cli publish     the merged policy's report in policy_versions.backtest_report
  backtest.history         labelled cases from the facts the gate worker stores (no look-ahead)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from backtest.history import load_history
from budgetguard.gate import evaluate
from budgetguard.policy import Policy
from budgetguard.records import build_inputs, to_gate_input
from tests.test_gate import facts

URL = os.environ.get("BG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="BG_TEST_DATABASE_URL not set")
PLATFORM = Path(__file__).resolve().parents[2]
SYNC = (URL or "").replace("+asyncpg", "+psycopg")


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=PLATFORM,
                   env={**os.environ, "BG_DATABASE_URL": URL or ""}, check=True)


def run(*args: str, env: dict[str, str]) -> None:
    subprocess.run([sys.executable, "-m", *args], cwd=PLATFORM, env={**os.environ, **env}, check=True)


def test_record_run_writes_one_eval_runs_row(tmp_path: Path) -> None:
    report = tmp_path / "template.json"
    run("evals.run_brief_eval", "--provider", "template", "--out", str(report), env={})
    sha = uuid.uuid4().hex
    run("evals.record_run", "--report", str(report), "--trigger", "manual", "--code-sha", sha,
        env={"BG_EVAL_DSN": SYNC})
    with create_engine(SYNC).connect() as c:
        row = c.execute(text("SELECT suite, provider, model, prompt_sha, cases, passed, metrics "
                             "FROM eval_runs WHERE code_sha = :s"), {"s": sha}).one()
    got = (row.suite, row.provider, row.model, row.cases, row.passed)
    assert got == ("brief", "template", None, 120, True)
    assert row.metrics["grounding"] == 1.0 and len(row.prompt_sha) == 12


def test_publish_stores_the_backtest_report(tmp_path: Path) -> None:
    for name in ("base", "cand"):
        run("backtest.cli", "score", "--policy", "../policy/freeze-policy.yaml", "--version", name * 5,
            "--out", str(tmp_path / f"{name}.json"), env={})
    report = tmp_path / "report.md"
    run("backtest.cli", "compare", "--base", str(tmp_path / "base.json"),
        "--cand", str(tmp_path / "cand.json"),
        "--report", str(report), env={})
    version = uuid.uuid4().hex
    run("backtest.cli", "publish", "--version", version, "--report", str(report), env={"BG_POLICY_DSN": SYNC})
    with create_engine(SYNC).connect() as c:
        md = c.execute(text("SELECT backtest_report ->> 'markdown' FROM policy_versions WHERE version = :v"),
                       {"v": version}).scalar_one()
    assert md.startswith("### Policy backtest") and "**PASS**" in md


def test_history_reads_stored_facts_and_hides_blocked_outcomes() -> None:
    engine = create_engine(SYNC)
    policy = Policy("itest-policy")
    with engine.begin() as c:
        c.execute(text("INSERT INTO policy_versions (version) VALUES ('itest-policy') "
                       "ON CONFLICT DO NOTHING"))
        ids = []
        for page in (False, True):                        # one admitted deploy, one blocked by a self page
            f = facts(case_id=uuid.uuid4().hex, page_firing=page, self_share_1h=0.9 if page else None)
            gi = to_gate_input(f, policy)
            d = evaluate(gi)
            ids.append(c.execute(text("""
                INSERT INTO decisions (service_id, github_delivery, sha, policy_version, inputs_hash, inputs,
                                       verdict, reason_code, canary_profile)
                SELECT id, :d, :sha, 'itest-policy', :h, CAST(:inputs AS jsonb), :v, :r, :cp
                FROM services WHERE name = 'orders-service' RETURNING id"""),
                {"d": f.case_id, "sha": "e" * 40, "h": gi.digest(),
                 "inputs": json.dumps(build_inputs(f, gi, policy, {}, {})), "v": str(d.verdict),
                 "r": d.reason_code, "cp": list(d.canary_profile)}).scalar_one())
        c.execute(text("""
            INSERT INTO deployments (service_id, decision_id, sha, started_at, finished_at, status,
                                     is_change_failure, change_failure_budget_pct)
            SELECT id, :dec, :sha, now(), now(), 'success', true, 2.5
            FROM services WHERE name = 'orders-service'"""),
            {"dec": ids[0], "sha": "e" * 40})
        since = c.execute(text("SELECT min(decided_at) FROM decisions WHERE id = ANY(:i)"),
                          {"i": ids}).scalar_one()
    with engine.connect() as c:
        cases = {cs.facts.case_id: cs for cs in load_history(c, since)}
    with engine.connect() as c:
        delivery = dict(c.execute(text("SELECT id, github_delivery FROM decisions WHERE id = ANY(:i)"),
                                  {"i": ids}).all())
    admitted, blocked = cases[delivery[ids[0]]], cases[delivery[ids[1]]]
    assert (admitted.outcome.was_change_failure, admitted.outcome.budget_cost_pct) == (True, 2.5)
    assert blocked.outcome.was_change_failure is None              # blocked in reality: never observed
    assert blocked.facts.page_firing is True and admitted.facts.service == "orders-service"
