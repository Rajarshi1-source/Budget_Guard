"""Exports labelled history for the backtest. Uses a read-only role (SELECT on decisions, deployments).

  python -m backtest.export_history --since 180d --out history.jsonl
"""
from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from .history import load_history, write_jsonl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="180d")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    since = datetime.now(UTC) - timedelta(days=int(a.since.rstrip("d")))
    engine = create_engine(os.environ["BG_READONLY_DSN"], pool_pre_ping=True)
    with engine.connect() as conn:
        cases = load_history(conn, since)
    write_jsonl(cases, a.out)
    print(f"exported {len(cases)} cases since {since:%Y-%m-%d}")


if __name__ == "__main__":
    main()
