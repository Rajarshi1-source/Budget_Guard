"""D-8 interop: Sloth parity files and the OpenSLO importer (W8.3)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from budgetguard.rulegen import generate
from budgetguard.rulegen.openslo import OpenSloError, import_openslo
from budgetguard.rulegen.sloth import _counter, parity_test, write_parity
from budgetguard.rulegen.spec import ServiceLevel, load

REPO = Path(__file__).resolve().parents[2]
SLO_DIR = REPO / "slo"

OPENSLO = """\
apiVersion: openslo/v1
kind: SLI
metadata: {name: checkout-errors}
spec:
  ratioMetric:
    counter: true
    bad:
      metricSource: {type: Prometheus, spec: {query: 'http_requests_total{job="checkout",code=~"5.."}'}}
    total:
      metricSource: {type: Prometheus, spec: {query: 'http_requests_total{job="checkout"}'}}
---
apiVersion: openslo/v1
kind: SLO
metadata: {name: checkout-availability}
spec:
  service: checkout
  indicatorRef: checkout-errors
  budgetingMethod: Occurrences
  timeWindow: [{duration: 30d, isRolling: true}]
  objectives: [{target: 0.999}]
---
apiVersion: openslo/v1
kind: SLO
metadata: {name: checkout-fast}
spec:
  service: checkout
  budgetingMethod: Occurrences
  timeWindow: [{duration: 30d, isRolling: true}]
  objectives: [{target: 0.99}]
  indicator:
    metadata: {name: fast}
    spec:
      ratioMetric:
        counter: true
        good:
          metricSource:
            type: Prometheus
            spec: {query: 'http_request_duration_seconds_bucket{job="checkout",le="0.3"}'}
        total:
          metricSource:
            type: Prometheus
            spec: {query: 'http_request_duration_seconds_count{job="checkout"}'}
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "slo.openslo.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_committed_parity_files_are_current() -> None:
    assert write_parity(SLO_DIR, REPO / "rules" / "parity", check=True) == 0


def test_every_sloth_rules_file_is_committed() -> None:
    for spec in sorted(SLO_DIR.glob("*.yaml")):
        assert (REPO / "rules" / "parity" / f"{spec.stem}.sloth.rules.yaml").is_file()


def test_parity_test_covers_every_slo_and_window() -> None:
    doc = yaml.safe_load(parity_test(load(SLO_DIR / "orders-service.yaml")))
    names = [t["name"].split(":")[0] for t in doc["tests"]]
    assert names == ["orders-service availability", "orders-service latency"]
    exprs = [e["expr"] for t in doc["tests"] for e in t["promql_expr_test"]]
    assert len(exprs) == 8 and all("sloth_service=\"orders-service\"" in e for e in exprs)


def test_counter_segments_are_continuous() -> None:
    # 0..40 by 4 (11 values), then +40 per step for 4 more values: 80, 120, 160, 200
    assert _counter([(10, 4.0), (5, 40.0)]) == "0+4x10 80+40x4"


def test_openslo_import_round_trips_through_the_generator(tmp_path: Path) -> None:
    out = import_openslo(_write(tmp_path, OPENSLO), job="checkout", min_events_per_hour=600, owner="team-pay")
    sl = ServiceLevel.model_validate(yaml.safe_load(out))
    assert sl.metadata.service == "checkout" and sl.metadata.owner == "team-pay"
    avail, fast = sl.spec.slos
    assert (avail.name, avail.objective, avail.sli.type) == ("checkout_availability", 0.999, "ratio")
    assert avail.sli.bad == 'http_requests_total{job="checkout",code=~"5.."}'
    assert fast.sli.type == "latency_bucket"
    assert fast.sli.good == 'http_request_duration_seconds_bucket{job="checkout",le="0.3"}'
    assert [t.burn for t in avail.alerting.page] == [14.4, 6]
    assert "ErrorBudgetBurnPage" in generate.render(sl, "prometheus")


@pytest.mark.parametrize(("old", "new", "why"), [
    ("budgetingMethod: Occurrences", "budgetingMethod: Timeslices", "not a request budget"),
    ('http_requests_total{job="checkout"}', 'sum(rate(http_requests_total{job="checkout"}[5m]))',
     "bare selector"),
    ("type: Prometheus, spec: {query: 'http_requests_total{job=\"checkout\",code",
     "type: Datadog, spec: {query: 'x{job=\"checkout\",code", "only Prometheus"),
    ("{duration: 30d, isRolling: true}", "{duration: 1M, isRolling: false}", "rolling"),
])
def test_openslo_refuses_what_it_cannot_represent(tmp_path: Path, old: str, new: str, why: str) -> None:
    assert old in OPENSLO
    with pytest.raises(OpenSloError, match=why):
        import_openslo(_write(tmp_path, OPENSLO.replace(old, new, 1)), job="checkout",
                       min_events_per_hour=600)
