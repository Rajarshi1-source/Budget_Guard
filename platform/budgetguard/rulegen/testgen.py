"""Generate promtool rule tests from the specs: every service, SLO and tier (budgetguard-slo-rules §3).

For each alert tier the test feeds a constant error ratio of 1.6x the tier's burn threshold and
expects the alert with its exact labels and annotations, plus the long- and short-window recording
rules it reads. A quiet case at 0.4x the lowest burn must not alert. (1.6/0.4 rather than 1.5/0.5
keep expected ratios off .5 rounding boundaries, where PromQL's half-up round() and Python differ.)
Hand-written edge cases
(traffic floor, dead target, failing probe) live next to the generated files.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from .generate import dump, seconds, sync_files
from .spec import ServiceLevel, Slo, Tier, load_dir

PER_MINUTE = 1000.0                     # 60,000 events/h: far above every traffic floor
FIRES, QUIET = 1.6, 0.4
_MATCHER = re.compile(r'(\w+)\s*(=~|!~|!=|=)\s*"((?:[^"\\]|\\.)*)"')
_CANDIDATES = ("500", "/test", "delivered", "self", "none")
_NEGATIVE = "x-test"


def concrete(selector: str, **override: str) -> tuple[str, dict[str, str]]:
    """Turn a PromQL selector into one concrete series it matches: (metric, labels)."""
    m = re.fullmatch(r"\s*([a-zA-Z_:][\w:]*)\s*\{(.*)\}\s*", selector)
    if not m:
        return selector.strip(), dict(override)
    name, body = m.groups()
    labels: dict[str, str] = {}
    for key, op, value in _MATCHER.findall(body):
        if op == "=":
            labels[key] = value
        elif op == "=~":
            labels[key] = next(c for c in _CANDIDATES if re.fullmatch(value, c))
        else:                                                    # != and !~: anything that does not match
            labels[key] = "/test" if not re.fullmatch(value, "/test") else _NEGATIVE
            if op == "!=" and labels[key] == value:
                labels[key] = _NEGATIVE
    labels.update(override)
    return name, labels


def _series(name: str, labels: dict[str, str]) -> str:
    return name + "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"


def _num(x: float) -> str:
    return f"{x:.10g}"


def promql_round(x: float, to: str = "0.0001") -> float:
    """PromQL round(x, to) rounds half up; Python's round() does not."""
    return float(Decimal(repr(x)).quantize(Decimal(to), rounding=ROUND_HALF_UP))


def _inputs(sl: ServiceLevel, slo: Slo, ratio: float, step_min: int, steps: int) -> list[dict[str, str]]:
    per_step = PER_MINUTE * step_min
    bad, good = per_step * ratio, per_step * (1 - ratio)
    if slo.sli.type == "ratio":
        name, base = concrete(slo.sli.total)
        assert slo.sli.bad is not None
        bad_name, bad_labels = concrete(slo.sli.self_bad or slo.sli.bad)
        series = [
            {"series": _series(name, {**base, "status": "200", "error_origin": "none"}),
             "values": f"0+{_num(good)}x{steps}"},
            {"series": _series(bad_name, bad_labels), "values": f"0+{_num(bad)}x{steps}"},
        ]
        # Real services publish histogram buckets next to the request counter. Without them a latency
        # SLI on the same counter would (correctly) read 100% bad and alert, so add all-fast buckets.
        for other in sl.spec.slos:
            if other.sli.type == "latency_bucket" and concrete(other.sli.total)[0] == name:
                assert other.sli.good is not None
                good_name, good_labels = concrete(other.sli.good)
                series.append({"series": _series(good_name, good_labels),
                               "values": f"0+{_num(per_step)}x{steps}"})
        return series
    assert slo.sli.good is not None
    total_name, total_labels = concrete(slo.sli.total)
    good_name, good_labels = concrete(slo.sli.good)
    return [
        {"series": _series(total_name, total_labels), "values": f"0+{_num(per_step)}x{steps}"},
        {"series": _series(good_name, good_labels), "values": f"0+{_num(good)}x{steps}"},
    ]


def _alert(sl: ServiceLevel, slo: Slo, severity: str) -> dict[str, Any]:
    svc = sl.metadata.service
    pace = "fast" if severity == "page" else "steadily"
    return {
        "exp_labels": {"severity": severity, "service": svc, "slo": slo.name,
                       "incident_type": f"{slo.name}_{'burn' if severity == 'page' else 'slow_burn'}"},
        "exp_annotations": {
            "summary": f"{svc} {slo.name} is spending error budget {pace}",
            "budgetguard_url": f"https://budgetguard.example.dev/services/{svc}"},
    }


def _step(tier_long: str) -> int:
    return 1 if seconds(tier_long) <= 6 * 3600 else 5         # 3-day windows at 5m resolution keep tests fast


def _fires(sl: ServiceLevel, slo: Slo, severity: str, tier: Tier) -> dict[str, Any]:
    a = 1 - slo.objective
    ratio = min(FIRES * tier.burn * a, 0.99)
    step = _step(tier.long)
    eval_min = seconds(tier.long) // 60 + 5
    steps = eval_min // step + 2
    svc = sl.metadata.service
    sel = f'{{service="{svc}",slo="{slo.name}"}}'
    expect = f'{{service="{svc}",slo="{slo.name}"}}'
    return {
        "name": f"{svc} {slo.name}: {tier.burn:g}x over {tier.long}/{tier.short} raises {severity}",
        "interval": f"{step}m",
        "input_series": _inputs(sl, slo, ratio, step, steps),
        "alert_rule_test": [{"eval_time": f"{eval_min}m",
                             "alertname": f"ErrorBudgetBurn{severity.capitalize()}",
                             "exp_alerts": [_alert(sl, slo, severity)]}],
        "promql_expr_test": [
            {"expr": f"round(bg:error_ratio:rate{w}{sel}, 0.0001)", "eval_time": f"{eval_min}m",
             "exp_samples": [{"labels": expect, "value": promql_round(ratio)}]}
            for w in dict.fromkeys((tier.long, tier.short))],
    }


def _quiet(sl: ServiceLevel, slo: Slo, severity: str, tiers: list[Tier]) -> dict[str, Any]:
    a = 1 - slo.objective
    ratio = QUIET * min(t.burn for t in tiers) * a
    longest = max(tiers, key=lambda t: seconds(t.long)).long
    step = _step(longest)
    eval_min = seconds(longest) // 60 + 5
    return {
        "name": f"{sl.metadata.service} {slo.name}: half the lowest {severity} burn stays quiet",
        "interval": f"{step}m",
        "input_series": _inputs(sl, slo, ratio, step, eval_min // step + 2),
        "alert_rule_test": [{"eval_time": f"{eval_min}m",
                             "alertname": f"ErrorBudgetBurn{severity.capitalize()}", "exp_alerts": []}],
    }


def test_file(sl: ServiceLevel, severity: str) -> str:
    tests: list[dict[str, Any]] = []
    for slo in sl.spec.slos:
        tiers = slo.alerting.page if severity == "page" else slo.alerting.ticket
        tests += [_fires(sl, slo, severity, t) for t in tiers]
        if tiers:
            tests.append(_quiet(sl, slo, severity, tiers))
    svc = sl.metadata.service
    doc = {"rule_files": [f"../../generated/{svc}.rules.yaml"], "evaluation_interval": "1m", "tests": tests}
    return f"# Generated by `bg tests` from slo/{svc}.yaml. Do not edit.\n" + dump(doc)


def write_all(slo_dir: Path, out: Path, check: bool = False) -> int:
    expected = {f"{sl.metadata.service}-{sev}.test.yaml": test_file(sl, sev)
                for sl in load_dir(slo_dir) for sev in ("page", "ticket")}
    return sync_files(out, expected, check, "*.test.yaml", "bg tests")
