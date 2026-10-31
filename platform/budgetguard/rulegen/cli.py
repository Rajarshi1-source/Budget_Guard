"""`bg` (pyproject: bg = "budgetguard.rulegen.cli:main"). Interface fixed by budgetguard-slo-rules:

  bg generate ../slo -o ../rules/generated [--check] [--format prometheus|prometheus-operator]
  bg tests ../slo -o ../rules/tests/generated [--check]
  bg export sloth ../slo/orders-service.yaml > orders.sloth.yaml
  bg parity ../slo -o ../rules/parity [--check]   # Sloth inputs + promtool parity tests (D-8)
  bg import openslo slo.openslo.yaml --job orders-service --min-events 600 > ../slo/orders.yaml
  bg status ../slo [--prometheus http://localhost:9090] [--json]   # live tri-state verdicts (D-4)
  bg sync-slos ../slo [--database-url URL]   # upsert services/slos rows; spec_hash detects drift
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
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
    pa = sub.add_parser("parity", help="Sloth specs and promtool parity tests (D-8)")
    pa.add_argument("slo_dir", type=Path)
    pa.add_argument("-o", "--out", type=Path, required=True)
    pa.add_argument("--check", action="store_true", help="fail if committed files differ")
    im = sub.add_parser("import", help="interop imports (ratio SLOs only)")
    im.add_argument("source", choices=("openslo",))
    im.add_argument("path", type=Path)
    im.add_argument("--job", required=True, help="Prometheus scrape job for the integrity alert")
    im.add_argument("--min-events", type=int, required=True, help="traffic floor per hour")
    st = sub.add_parser("status", help="live HEALTHY / AT_RISK / UNKNOWN per service and SLO")
    st.add_argument("slo_dir", type=Path)
    st.add_argument("--prometheus", default="http://localhost:9090")
    st.add_argument("--json", action="store_true")
    sy = sub.add_parser("sync-slos", help="upsert services and slos rows from the specs")
    sy.add_argument("slo_dir", type=Path)
    sy.add_argument("--database-url", default=None, help="default: BG_DATABASE_URL")
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
    if a.cmd == "parity":
        from .sloth import write_parity
        return write_parity(a.slo_dir, a.out, check=a.check)
    if a.cmd == "import":
        from .openslo import import_openslo
        sys.stdout.write(import_openslo(a.path, job=a.job, min_events_per_hour=a.min_events))
        return 0
    if a.cmd == "status":
        return asyncio.run(_status(a.slo_dir, a.prometheus, a.json))
    return asyncio.run(_sync(a.slo_dir, a.database_url))


async def _sync(slo_dir: Path, url: str | None) -> int:
    from ..db import make_engine
    from ..slo_registry import sync_slos
    from .spec import load_dir

    engine = make_engine(url, pool_size=1)
    try:
        async with engine.begin() as conn:
            result = await sync_slos(conn, load_dir(slo_dir))
    finally:
        await engine.dispose()
    print(f"synced {len(result.ids)} slos ({result.inserted} new, {result.deactivated} deactivated)")
    return 0


async def _status(slo_dir: Path, url: str, as_json: bool) -> int:
    import httpx

    from ..integrations.prometheus import PrometheusClient
    from ..signals import service_statuses
    from .spec import load_dir

    async with httpx.AsyncClient() as http:
        rows = await service_statuses(PrometheusClient(url, http), load_dir(slo_dir))
    if as_json:
        print(json.dumps([asdict(r) for r in rows], indent=2))
    else:
        print(f"{'service':22} {'slo':12} {'state':8} {'integrity':11} "
              f"{'remaining%':>10} {'burn1h':>7}  alerts")
        for r in rows:
            rem = "-" if r.remaining_pct is None else f"{r.remaining_pct:.2f}"
            burn = "-" if r.burn_rate_1h is None else f"{r.burn_rate_1h:.2f}"
            firing = (("page", r.page_firing), ("ticket", r.ticket_firing))
            alerts = ",".join(s for s, on in firing if on) or "-"
            print(f"{r.service:22} {r.slo:12} {r.state:8} {r.integrity:11} {rem:>10} {burn:>7}  {alerts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
