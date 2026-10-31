"""Import OpenSLO v1 SLOs as a BudgetGuard ServiceLevel spec (D-8, W8). Ratio SLOs only.

Accepted: `kind: SLO` documents (with an inline `indicator` or an `indicatorRef` to a `kind: SLI`
document in the same file) whose `ratioMetric` is a counter with Prometheus sources, `bad` + `total`
or `good` + `total`, `budgetingMethod: Occurrences`, and one rolling 30d or 28d window.

Refused, with the reason: Timeslices budgeting (a time-slice budget is not a request budget, so the
gate's price would be in the wrong unit), threshold metrics, calendar windows, non-Prometheus
sources, and queries that are not a bare selector (the generator wraps every selector in rate() and
increase() per window itself; a pre-aggregated query would be aggregated twice).

OpenSLO alert policies are not imported: the Workbook tiers below are applied, and the spec is the
place to change them. The result goes through the same model `bg generate` loads, so an import that
would not generate never prints.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .generate import dump
from .spec import ServiceLevel

_SELECTOR = re.compile(r"^\s*[a-zA-Z_:][\w:]*\s*(\{[^{}()]*\})?\s*$")
_WORKBOOK = {
    "page": [{"long": "1h", "short": "5m", "burn": 14.4}, {"long": "6h", "short": "30m", "burn": 6}],
    "ticket": [{"long": "3d", "short": "6h", "burn": 1}],
}


class OpenSloError(ValueError):
    """An OpenSLO document BudgetGuard cannot represent faithfully."""


def _query(source: Any, where: str) -> str:
    if not isinstance(source, dict):
        raise OpenSloError(f"{where}: missing")
    ms = source.get("metricSource") or {}
    if str(ms.get("type", "")).lower() != "prometheus":
        raise OpenSloError(f"{where}: only Prometheus metric sources are supported, got {ms.get('type')!r}")
    q = str((ms.get("spec") or {}).get("query", ""))
    if not _SELECTOR.match(q):
        raise OpenSloError(f"{where}: query must be a bare selector such as "
                           f'http_requests_total{{job="api"}}, got {q!r}')
    return q.strip()


def _slo_name(name: str) -> str:
    out = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")
    return out if re.match(r"^[a-z]", out) else f"slo_{out}"


def _sli(doc: dict[str, Any], indicators: dict[str, dict[str, Any]]) -> dict[str, Any]:
    spec = doc["spec"]
    if "indicatorRef" in spec:
        ref = spec["indicatorRef"]
        if ref not in indicators:
            raise OpenSloError(f"SLO {doc['metadata']['name']}: indicatorRef {ref!r} not in this file")
        indicator = indicators[ref]
    else:
        indicator = spec.get("indicator") or {}
    ratio = (indicator.get("spec") or {}).get("ratioMetric")
    where = f"SLO {doc['metadata']['name']}"
    if not ratio:
        raise OpenSloError(f"{where}: only ratioMetric SLIs are supported "
                           "(thresholdMetric is not a request budget)")
    if ratio.get("counter") is not True:
        raise OpenSloError(f"{where}: ratioMetric.counter must be true (counters, not gauges)")
    total = _query(ratio.get("total"), f"{where} total")
    if ratio.get("bad"):
        return {"type": "ratio", "total": total, "bad": _query(ratio["bad"], f"{where} bad")}
    if ratio.get("good"):
        # 1 - good/total: the generator's good-over-total form (named for its usual latency use)
        return {"type": "latency_bucket", "total": total, "good": _query(ratio["good"], f"{where} good")}
    raise OpenSloError(f"{where}: ratioMetric needs bad or good next to total")


def _window(doc: dict[str, Any]) -> str:
    spec, where = doc["spec"], f"SLO {doc['metadata']['name']}"
    if spec.get("budgetingMethod", "Occurrences") != "Occurrences":
        raise OpenSloError(f"{where}: budgetingMethod {spec['budgetingMethod']} is not a request budget; "
                           "only Occurrences maps to BudgetGuard's pricing")
    windows = spec.get("timeWindow") or []
    if len(windows) != 1 or windows[0].get("isRolling") is not True:
        raise OpenSloError(f"{where}: exactly one rolling timeWindow is supported")
    duration = str(windows[0].get("duration", ""))
    if duration not in ("30d", "28d"):
        raise OpenSloError(f"{where}: window {duration!r} must be 30d or 28d")
    return duration


def import_openslo(path: Path, job: str, min_events_per_hour: int, owner: str | None = None) -> str:
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
    for d in docs:
        if d.get("apiVersion") != "openslo/v1":
            name = (d.get("metadata") or {}).get("name")
            raise OpenSloError(f"{d.get('kind')} {name}: apiVersion must be openslo/v1")
    indicators = {d["metadata"]["name"]: d for d in docs if d.get("kind") == "SLI"}
    slo_docs = [d for d in docs if d.get("kind") == "SLO"]
    if not slo_docs:
        raise OpenSloError(f"{path}: no kind: SLO documents")
    services = {d["spec"]["service"] for d in slo_docs}
    if len(services) != 1:
        raise OpenSloError(f"{path}: one service per spec file, found {sorted(services)}")
    windows = {_window(d) for d in slo_docs}
    if len(windows) != 1:
        raise OpenSloError(f"{path}: all SLOs of a service share one window, found {sorted(windows)}")

    slos = []
    for d in slo_docs:
        objectives = d["spec"].get("objectives") or []
        if len(objectives) != 1 or "target" not in objectives[0]:
            raise OpenSloError(f"SLO {d['metadata']['name']}: exactly one objective "
                               "with a target is supported")
        slos.append({"name": _slo_name(d["metadata"]["name"]), "objective": float(objectives[0]["target"]),
                     "sli": _sli(d, indicators), "alerting": _WORKBOOK})
    service = services.pop()
    doc = {
        "apiVersion": "budgetguard.dev/v1", "kind": "ServiceLevel",
        "metadata": {"service": service, "owner": owner or "unowned"},
        "spec": {"window": windows.pop(), "failure_mode": "fail_open",
                 "min_events_per_hour": min_events_per_hour, "job": job, "slos": slos,
                 "gate": {"reserve_pct": 5, "spend_fraction": 0.5, "hard_stop_on": ["page"]}},
    }
    ServiceLevel.model_validate(doc)          # never print a spec `bg generate` would reject
    head = f"# Imported by `bg import openslo` from {path.name}. Review owner, failure_mode and probe.\n"
    return head + dump(doc)
