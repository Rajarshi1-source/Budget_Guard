"""Generator guarantees (budgetguard-slo-rules): every referenced window exists, output is stable,
hand edits are caught, and bad specs fail loudly."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from budgetguard.rulegen import generate, testgen
from budgetguard.rulegen.cli import main
from budgetguard.rulegen.sloth import export_sloth
from budgetguard.rulegen.spec import ServiceLevel, load, load_dir

REPO = Path(__file__).resolve().parents[2]
SLO_DIR = REPO / "slo"
SPECS = load_dir(SLO_DIR)


def test_all_four_services_have_specs() -> None:
    assert [s.metadata.service for s in SPECS] == [
        "auth-service", "inventory-service", "notifications-service", "orders-service"]


@pytest.mark.parametrize("sl", SPECS, ids=lambda s: s.metadata.service)
def test_every_window_an_alert_references_has_a_recording_rule(sl: ServiceLevel) -> None:
    recorded = {(r["record"], r["labels"]["slo"]) for g in generate.recording_groups(sl) for r in g["rules"]}
    for rule in generate.alert_groups(sl)[0]["rules"]:
        for name, slo in re.findall(r'(bg:[a-z_]+:[a-z0-9]+)\{service="[^"]+",slo="([^"]+)"\}', rule["expr"]):
            assert (name, slo) in recorded, \
                f"{rule['alert']} reads {name} for {slo}, which nothing records (B-4)"


@pytest.mark.parametrize("sl", SPECS, ids=lambda s: s.metadata.service)
def test_gate_and_dashboard_windows_always_exist(sl: ServiceLevel) -> None:
    recorded = {r["record"] for g in generate.recording_groups(sl) for r in g["rules"]}
    for w in ("5m", "30m", "1h", "6h", "3d"):
        assert f"bg:error_ratio:rate{w}" in recorded
        assert f"bg:self_error_ratio:rate{w}" in recorded           # availability carries self_bad (D-3)
    assert {"bg:slo_allowed_ratio", "bg:requests:rate1h", "bg:error_ratio:increase30d"} <= recorded


def test_thresholds_match_the_workbook_ladder() -> None:
    orders = next(s for s in SPECS if s.metadata.service == "orders-service")
    page = next(r for r in generate.alert_groups(orders)[0]["rules"] if r["alert"] == "ErrorBudgetBurnPage")
    assert "(14.4 * 0.001)" in page["expr"] and "(6 * 0.001)" in page["expr"]
    assert ">= 600" in page["expr"]                                    # traffic floor
    assert "for" not in page                                           # the short window is the delay (B-12)
    integrity = generate.alert_groups(orders)[0]["rules"][-1]
    assert integrity["alert"] == "SLIIntegrityLost" and integrity["for"] == "2m"
    assert 'probe_success{job="blackbox",service="orders-service"} == 0' in integrity["expr"]


def test_error_numerators_zero_fill_while_there_is_traffic() -> None:
    orders = next(s for s in SPECS if s.metadata.service == "orders-service")
    rules = {(r["record"], r["labels"]["slo"]): r["expr"]
             for g in generate.recording_groups(orders) for r in g["rules"]}
    for key in (("bg:error_ratio:rate5m", "availability"), ("bg:self_error_ratio:rate5m", "availability"),
                ("bg:error_ratio:rate5m", "latency")):
        assert "\nor\n0 * sum by (service)" in rules[key], f"{key} would be empty, not 0, with zero errors"


def test_output_is_byte_stable_and_free_of_yaml_anchors() -> None:
    for sl in SPECS:
        first, second = generate.render(sl), generate.render(sl)
        assert first == second
        assert "&id" not in first and "*id" not in first
        yaml.safe_load(first)


def test_committed_rules_and_tests_match_the_specs() -> None:
    assert generate.write_all(SLO_DIR, REPO / "rules" / "generated", check=True) == 0
    assert testgen.write_all(SLO_DIR, REPO / "rules" / "tests" / "generated", check=True) == 0


def test_check_catches_a_hand_edit_and_a_stray_file(tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "rules"
    assert generate.write_all(SLO_DIR, out) == 0
    target = out / "orders-service.rules.yaml"
    edited = target.read_text(encoding="utf-8").replace("14.4 * 0.001", "20 * 0.001")
    target.write_text(edited, encoding="utf-8")
    (out / "handmade.rules.yaml").write_text("groups: []\n", encoding="utf-8")
    assert generate.write_all(SLO_DIR, out, check=True) == 1
    err = capsys.readouterr().err
    assert "hand-edited" in err and "handmade.rules.yaml" in err and "-" in err
    assert main(["generate", str(SLO_DIR), "-o", str(out)]) == 0         # regenerating repairs it
    assert generate.write_all(SLO_DIR, out, check=True) == 0
    assert not (out / "handmade.rules.yaml").exists()


def test_prometheus_operator_format_wraps_groups_in_a_prometheusrule() -> None:
    doc = yaml.safe_load(generate.render(SPECS[0], "prometheus-operator"))
    assert doc["kind"] == "PrometheusRule" and doc["metadata"]["labels"]["release"] == "kube-prometheus-stack"
    assert doc["spec"]["groups"]


def _spec_dict() -> dict:
    return yaml.safe_load((SLO_DIR / "orders-service.yaml").read_text(encoding="utf-8"))


def test_typo_in_a_spec_is_rejected() -> None:
    d = _spec_dict()
    d["spec"]["min_event_per_hour"] = d["spec"].pop("min_events_per_hour")
    with pytest.raises(ValidationError):
        ServiceLevel.model_validate(d)


def test_le_must_be_in_float_form() -> None:
    d = _spec_dict()
    d["spec"]["slos"][1]["sli"]["good"] = d["spec"]["slos"][1]["sli"]["good"].replace('le="0.5"', 'le="1"')
    with pytest.raises(ValidationError, match="float form"):
        ServiceLevel.model_validate(d)


def test_burn_that_needs_more_than_100_percent_errors_is_rejected() -> None:
    d = _spec_dict()
    d["spec"]["slos"][0]["objective"] = 0.9
    d["spec"]["slos"][0]["alerting"]["page"][0]["burn"] = 14.4      # 14.4 * 0.1 = 144% errors
    with pytest.raises(ValidationError, match="100%"):
        ServiceLevel.model_validate(d)


def test_concrete_series_match_their_selectors() -> None:
    name, labels = testgen.concrete(
        'http_server_requests_seconds_count{service="orders-service",uri!~"/actuator.*",status=~"5.."}')
    assert name == "http_server_requests_seconds_count"
    assert labels == {"service": "orders-service", "uri": "/test", "status": "500"}


def test_promql_round_is_half_up() -> None:
    assert testgen.promql_round(0.00075) == 0.0008                     # Python's round() says 0.0007


def test_sloth_export_maps_objective_and_queries(tmp_path: Path) -> None:
    doc = yaml.safe_load(export_sloth(SLO_DIR / "orders-service.yaml"))
    avail = doc["slos"][0]
    assert doc["version"] == "prometheus/v1" and doc["service"] == "orders-service"
    assert avail["objective"] == 99.9
    assert "{{.window}}" in avail["sli"]["events"]["error_query"]
    assert avail["alerting"]["page_alert"]["disable"] is True


def test_spec_files_load_individually(tmp_path: Path) -> None:
    copy = tmp_path / "x.yaml"
    shutil.copyfile(SLO_DIR / "auth-service.yaml", copy)
    assert load(copy).spec.slos[0].objective == 0.9995


def test_sync_slos_is_not_available_yet() -> None:
    assert main(["sync-slos"]) == 2
