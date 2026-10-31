from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from budgetguard.briefs import brief_view
from budgetguard.grounding import check
from budgetguard.policy import Policy
from budgetguard.rulegen.spec import load_dir
from budgetguard.workers.brief_writer import brief_context, decision_row_dict

ROOT = Path(__file__).resolve().parents[2]
SPECS = {sl.metadata.service: sl for sl in load_dir(ROOT / "slo")}
URL = "https://budgetguard.example.dev/decisions/12"


def row(**kw: Any) -> SimpleNamespace:
    inputs = {"facts": {"lane": None}, "gate_input": {"remaining_pct": 3.2, "reserve_pct": 5.0},
              "lane_evidence": {}, "eta": {"eta_utc": "2026-11-01T14:20Z", "eta_in_hours": 26.5}}
    base: dict[str, Any] = dict(id=12, service="orders-service", sha="9f3c2ab" + "0" * 33, verdict="block",
                                reason_code="OVER_BUDGET", price_pct=1.26, affordable_pct=0.0,
                                canary_profile=[], inputs=inputs)
    return SimpleNamespace(**{**base, **kw})


def view_of(r: SimpleNamespace, policy: Policy | None = None) -> dict[str, Any]:
    ctx = brief_context(r, SPECS.get(r.service), policy or Policy("v1"), URL)
    return brief_view(decision_row_dict(r), ctx)


def test_over_budget_block_names_the_service_and_the_recorded_eta() -> None:
    v = view_of(row())
    assert (v["culprit"], v["eta_utc"], v["eta_in_hours"], v["remaining_pct"]) == \
        ("orders-service", "2026-11-01T14:20Z", 26.5, 3.2)
    assert v["objective_pct"] == 99.9 and v["still_allowed"] == ["rollback to a previously healthy build"]


def test_admit_names_no_cause_and_lists_nothing_extra() -> None:
    v = view_of(row(verdict="admit", reason_code="WITHIN_BUDGET", canary_profile=[25, 100]))
    assert v["culprit"] is None and v["still_allowed"] == []


def test_charge_victim_names_no_culprit() -> None:
    assert view_of(row(), Policy("v1", attribution="charge_victim"))["culprit"] is None


def test_incident_fix_lane_carries_the_incident_id() -> None:
    r = row(verdict="admit", reason_code="INCIDENT_FIX_LANE", canary_profile=[5, 25, 50, 100])
    r.inputs = {**r.inputs, "facts": {"lane": "incident_fix"}, "lane_evidence": {"incident_id": 77}}
    v = view_of(r)
    assert (v["lane"], v["incident_id"]) == ("incident_fix", "INC-77")


def test_every_context_shape_gives_a_grounded_template_brief() -> None:
    from budgetguard.briefs import template_brief
    shapes = [row(), row(verdict="admit", reason_code="WITHIN_BUDGET", price_pct=0.4, affordable_pct=20.0),
              row(reason_code="ACTIVE_PAGE", price_pct=None, affordable_pct=None),
              row(verdict="admit_unverified", reason_code="SLI_UNKNOWN", price_pct=None, affordable_pct=None)]
    for r in shapes:
        r.inputs = {**r.inputs, "eta": None} if r.reason_code != "OVER_BUDGET" else r.inputs
        v = view_of(r)
        result = check(template_brief(v), v)
        assert result.passed and result.fact_recall == 1.0, (r.reason_code, result)


def test_eta_json_round_trips_into_the_view() -> None:
    from budgetguard.briefs import Eta
    from budgetguard.workers.gate_worker import _eta_json
    now = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    e = _eta_json(Eta(datetime(2026, 11, 1, 14, 30, tzinfo=UTC), None), now)
    assert e == {"eta_utc": "2026-11-01T14:30Z", "eta_in_hours": 26.5, "at_half_budget_utc": None}
    assert _eta_json(Eta(None, None), now) is None and _eta_json(None, now) is None
