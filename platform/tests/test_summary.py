from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from budgetguard.eta import BUCKET, Bucket
from budgetguard.integrity import Integrity, SliState
from budgetguard.rulegen.spec import load
from budgetguard.signals import SloStatus
from budgetguard.summary import SloInputs, Window, rolling_daily, service_card

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
ORDERS = load(Path(__file__).resolve().parents[2] / "slo" / "orders-service.yaml")   # reserve 5%


def status(slo: str, *, integrity: Integrity = Integrity.OK, page: bool = False,
           rule_remaining: float | None = 90.0) -> SloStatus:
    return SloStatus("orders-service", slo, integrity, SliState.HEALTHY, rule_remaining, 0.5,
                     page, False, 9000)


def eta_window(bad_days: int) -> list[Bucket]:
    """30 days at 100 req/bucket; the oldest `bad_days` at 1% errors (0.999 SLO -> exactly 0% left at 3)."""
    start = NOW - timedelta(days=30)
    n, k = 30 * 288, bad_days * 288
    return [Bucket(start + i * BUCKET, 1.0 if i < k else 0.0, 100.0) for i in range(n)]


def card(avail: SloInputs, latency: SloInputs | None = None) -> dict:
    return service_card(ORDERS, {"availability": avail,
                                 "latency": latency or SloInputs(status("latency"), Window(1e6, 0, 0))},
                        now=NOW, open_incident=None, last_decision=None)


def test_healthy_service() -> None:
    c = card(SloInputs(status("availability"), Window(1e6, 300, 100)))
    assert (c["state"], c["frozen"], c["unfreeze_eta"]) == ("healthy", False, None)
    assert c["remaining_pct"] == pytest.approx(70.0)
    assert c["remaining_self_pct"] == pytest.approx(90.0)


def test_budget_freeze_projects_an_eta() -> None:
    w = eta_window(3)
    c = card(SloInputs(status("availability"), Window(864_000, 864, 864), eta_buckets=w))
    assert c["frozen"] and c["state"] == "at_risk"
    # 5% reserve: 43.2 bad requests' worth of buckets must age out (44 buckets)
    assert datetime.fromisoformat(c["unfreeze_eta"]) - w[-1].start == timedelta(minutes=5 * 45)


def test_page_freeze_has_no_eta() -> None:
    c = card(SloInputs(status("availability", page=True), Window(864_000, 864, 864),
                       eta_buckets=eta_window(3), self_share_1h=1.0))
    assert c["frozen"] and c["unfreeze_eta"] is None


def test_burning_latency_turns_the_card_at_risk() -> None:
    c = card(SloInputs(status("availability"), Window(1e6, 0, 0)),
             SloInputs(status("latency", page=True), Window(1e6, 0, 0), self_share_1h=1.0))   # no origin
    assert (c["state"], c["frozen"]) == ("at_risk", True)
    assert c["remaining_pct"] == pytest.approx(100.0)          # numbers stay the availability SLO's


def test_dead_signal_is_unknown_and_reported_as_the_worst_integrity() -> None:
    c = card(SloInputs(status("availability", integrity=Integrity.STALE), Window(1e6, 0, 0)),
             SloInputs(status("latency", integrity=Integrity.LOW_TRAFFIC), Window(1e6, 0, 0)))
    assert (c["state"], c["integrity"]) == ("unknown", "stale")


def test_no_buckets_falls_back_to_the_recording_rule() -> None:
    c = card(SloInputs(status("availability", rule_remaining=42.0), None))
    assert c["remaining_pct"] == 42.0 and c["remaining_self_pct"] is None


def test_rolling_daily_sums_counts_over_30_days() -> None:
    today = NOW.date()
    daily = [(today - timedelta(days=40), 100.0, 100_000.0),        # only the oldest windows see it
             (today - timedelta(days=2), 50.0, 100_000.0),
             (today, 0.0, 100_000.0)]
    out = rolling_daily(0.999, daily, today)
    assert len(out) == 30
    assert out[0] == pytest.approx(0.0)                             # window -58..-29 still holds day -40
    assert out[-3] == pytest.approx(50.0)                           # 50 / 100k = 0.0005 -> half the budget
    assert out[-1] == pytest.approx(100 * (1 - (50 / 200_000) / 0.001))


def test_rolling_daily_without_history_is_none() -> None:
    assert rolling_daily(0.999, [], date(2026, 10, 26)) == [None] * 30


def test_a_dependency_caused_page_does_not_freeze_the_card_under_charge_origin() -> None:
    victim = SloInputs(status("availability", page=True), Window(1e6, 900, 9), self_share_1h=0.01)
    c = card(victim)
    assert c["frozen"] is False and c["state"] == "at_risk"          # users felt it: at risk, not frozen
    own = SloInputs(status("availability", page=True), Window(1e6, 900, 9), self_share_1h=0.9)
    assert card(own)["frozen"] is True
    unknown = SloInputs(status("availability", page=True), Window(1e6, 900, 9))
    assert card(unknown)["frozen"] is False                           # no share: nothing self-inflicted
