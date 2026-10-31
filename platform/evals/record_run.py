"""Writes one eval run to eval_runs. Keyed by (suite, model, prompt_sha) so a prompt edit is
versioned exactly like a model change.

  python -m evals.record_run --report reports/brief.json --trigger pr --code-sha $GITHUB_SHA
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text

INSERT = text("""
INSERT INTO eval_runs (suite, provider, model, prompt_sha, code_sha, cases, metrics, passed, trigger)
VALUES ('brief', :provider, :model, :prompt_sha, :code_sha, :cases, CAST(:metrics AS jsonb), :passed,
        :trigger)
""")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--trigger", choices=("pr", "nightly", "manual"), required=True)
    ap.add_argument("--code-sha", required=True)
    a = ap.parse_args()
    s = json.loads(Path(a.report).read_text(encoding="utf-8"))["summary"]
    engine = create_engine(os.environ["BG_EVAL_DSN"])
    with engine.begin() as conn:
        conn.execute(INSERT, {"provider": s["provider"], "model": s["model"], "prompt_sha": s["prompt_sha"],
                              "code_sha": a.code_sha, "cases": s["cases"], "metrics": json.dumps(s),
                              "passed": not s["failures"], "trigger": a.trigger})


if __name__ == "__main__":
    main()
