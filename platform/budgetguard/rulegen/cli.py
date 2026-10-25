"""`bg` (pyproject: bg = "budgetguard.rulegen.cli:main"). Interface fixed by budgetguard-slo-rules:

  bg generate ../slo -o ../rules/generated [--check] [--format prometheus|prometheus-operator]
  bg tests ../slo -o ../rules/tests/generated [--check]
  bg export sloth ../slo/orders-service.yaml > orders.sloth.yaml
  bg sync-slos                      # upsert slos rows; spec_hash detects drift (W4)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bg")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="slo specs -> Prometheus rule files")
    g.add_argument("slo_dir", type=Path)
    g.add_argument("-o", "--out", type=Path, required=True)
    g.add_argument("--format", choices=("prometheus", "prometheus-operator"), default="prometheus")
    g.add_argument("--check", action="store_true", help="fail if committed rules differ")
    t = sub.add_parser("tests", help="slo specs -> promtool rule tests (every service, SLO and tier)")
    t.add_argument("slo_dir", type=Path)
    t.add_argument("-o", "--out", type=Path, required=True)
    t.add_argument("--check", action="store_true", help="fail if committed tests differ")
    e = sub.add_parser("export", help="interop exports")
    e.add_argument("target", choices=("sloth",))
    e.add_argument("spec", type=Path)
    sub.add_parser("sync-slos", help="upsert slos rows from the specs (available from W4)")
    a = ap.parse_args(argv)

    if a.cmd == "generate":
        from .generate import write_all
        return write_all(a.slo_dir, a.out, fmt=a.format, check=a.check)
    if a.cmd == "tests":
        from .testgen import write_all as write_tests
        return write_tests(a.slo_dir, a.out, check=a.check)
    if a.cmd == "export":
        from .sloth import export_sloth
        sys.stdout.write(export_sloth(a.spec))
        return 0
    print("bg sync-slos needs the database layer (plan W4.3); not available yet", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
