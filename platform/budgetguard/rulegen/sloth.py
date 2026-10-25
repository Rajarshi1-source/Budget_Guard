"""Export a ServiceLevel spec as a Sloth `prometheus/v1` spec for the parity check (D-8, W8).

Sloth then generates its own rules from the same queries; a promtool test asserts both generators
produce the same ratios (budgetguard-slo-rules §4). Sloth's alerts are disabled: BudgetGuard's own
alerts are the ones that page.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .generate import dump
from .spec import Slo, load


def _queries(slo: Slo) -> dict[str, str]:
    total = f"sum(rate({slo.sli.total}[{{{{.window}}}}]))"
    if slo.sli.type == "ratio":
        return {"error_query": f"sum(rate({slo.sli.bad}[{{{{.window}}}}]))", "total_query": total}
    good = f"sum(rate({slo.sli.good}[{{{{.window}}}}]))"
    return {"error_query": f"({total} - {good})", "total_query": total}


def export_sloth(spec_path: Path) -> str:
    sl = load(spec_path)
    doc: dict[str, Any] = {
        "version": "prometheus/v1",
        "service": sl.metadata.service,
        "labels": {"owner": sl.metadata.owner},
        "slos": [{
            "name": slo.name,
            "objective": round(slo.objective * 100, 6),
            "sli": {"events": _queries(slo)},
            "alerting": {"name": f"Sloth{slo.name.capitalize()}",
                         "page_alert": {"disable": True}, "ticket_alert": {"disable": True}},
        } for slo in sl.spec.slos],
    }
    return dump(doc)
